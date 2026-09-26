"""深層学習による音声強調 (要: uv sync --extra dnn).

C. Subakan et al., "Attention is all you need in speech separation," ICASSP 2021
(SepFormer) を DNS Challenge 4 で音声強調用に学習した SpeechBrain の公開モデル
(speechbrain/sepformer-dns4-16k-enhancement) を使う. DNS の雑音には突発音
(ドア・食器・キーボードなど) が含まれ, 声と重なった状態で学習されているため,
MCRA + 周期性ゲート (denoise.py) では分けられない「声と同時に鳴った物音」に対応できる.

モデル出力は大きさ・符号が不定なので, 入力への最小二乗射影でゲインを合わせる.
環境音は 入力 − 音声 として求めるので, 足すと入力に戻る.

処理は発話ごとに切り出して行い (enhance_checked), 従来法の周期性ゲート
(声の区間の外を -25 dB) を掛ける. 評価:
- 合成 (experiments/eval_overlap.py, 正解の声あり, 3 配置): SI-SDR 従来法 13.7 → 18.6 dB,
  声と重なった突発音の漏れ -3.1 → -6.5 dB, 声から離れた突発音の漏れ -15.9 → -50 dB 前後.
- 実録音 protocol の「声と同時に机」2 回: 声側に残る割合 37.5% / 98.5% → 6.3% / 7.2%.
  全実録音 32 発話で従来法への置き換え 0 件.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL = "speechbrain/sepformer-dns4-16k-enhancement"
SR = 16000


def _device() -> str:
    """Apple Silicon の GPU (MPS) があれば使う. M4 で CPU 比 1.7 倍速, 出力差は相対 1e-4 未満."""
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=1)
def _model():
    from speechbrain.inference.separation import SepformerSeparation

    save = Path.home() / ".cache" / "voicehack" / MODEL.split("/")[-1]
    return SepformerSeparation.from_hparams(source=MODEL, savedir=str(save),
                                            run_opts={"device": _device()})


def _enhance_chunk(x: np.ndarray) -> np.ndarray:
    import torch

    with torch.no_grad():
        inp = torch.tensor(x, dtype=torch.float32)[None].to(_device())
        y = _model().separate_batch(inp)[0, :, 0].cpu().numpy()
    y = y[: len(x)].astype(np.float64)
    den = float(np.dot(y, y))
    return y * (float(np.dot(x, y)) / den) if den > 0 else y


def enhance(x: np.ndarray, sr: int, chunk_s: float = 4.0, overlap_s: float = 0.5) -> np.ndarray:
    """音声成分を推定して返す (入力と同じ sr・長さ). 重ね合わせ窓で chunk_s 秒ずつ処理.

    区間を長く (20 s) すると, 入力が 0 dBFS を超える大きな突発音 (拍手・机) を含む
    録音で, 同じ区間の声まで 15〜30 dB 消える失敗が実録音 32 区間中 3 区間で起きた.
    区間全体で決まる正規化やゲインが突発音に引きずられるためと考えられる.
    4 s にすると 3 区間とも回復 (従来法比 -1.6〜+0.8 dB) し, 他の区間は変わらない.
    """
    from .audio_io import resample

    y16 = resample(x, sr, SR) if sr != SR else x
    n, c, o = len(y16), int(chunk_s * SR), int(overlap_s * SR)
    if n <= c:
        out = _enhance_chunk(y16)
    else:
        out = np.zeros(n)
        wsum = np.zeros(n)
        step = c - o
        for s in range(0, n, step):
            e = min(n, s + c)
            w = np.ones(e - s)
            if s > 0:
                w[:o] = np.linspace(0, 1, o)
            if e < n:
                w[-o:] = np.linspace(1, 0, o)
            out[s:e] += _enhance_chunk(y16[s:e]) * w
            wsum[s:e] += w
            if e == n:
                break
        out /= np.maximum(wsum, 1e-9)
    if sr != SR:
        out = resample(out, SR, sr)
    return out[: len(x)] if len(out) >= len(x) else np.pad(out, (0, len(x) - len(out)))


def gate_by_voice_region(x: np.ndarray, speech: np.ndarray, sr: int,
                         g_min_db: float = -25.0, sep=None) -> np.ndarray:
    """DNN の音声推定に, 周期性に基づく「声の区間」ゲート (denoise.voice_region) を掛ける.
    声から離れた突発音は従来法のゲートの方がよく落とせるため, 両者を組み合わせる."""
    from .denoise import separate

    sep = sep if sep is not None else separate(x, sr)
    gmin = 10 ** (g_min_db / 20)
    t = np.arange(len(x)) / sr
    region = np.interp(t, sep.S.t, sep.voice_region, left=0.0, right=0.0)
    return speech * (gmin + (1 - gmin) * region)


def _voiced_lowband_ratio_db(y: np.ndarray, ref: np.ndarray, mask: np.ndarray, sos) -> float:
    from scipy.signal import sosfiltfilt

    if mask.sum() < 10:
        return 0.0
    a, b = sosfiltfilt(sos, y)[mask], sosfiltfilt(sos, ref)[mask]
    return float(10 * np.log10((np.sum(a ** 2) + 1e-20) / (np.sum(b ** 2) + 1e-20)))


def _voiced_lowband_check(y, ref, mask, sos) -> tuple[float, float]:
    """有声フレーム・1 kHz 以下での (エネルギー比 dB, 相関)."""
    from scipy.signal import sosfiltfilt

    if mask.sum() < 10:
        return 0.0, 1.0
    a, b = sosfiltfilt(sos, y)[mask], sosfiltfilt(sos, ref)[mask]
    r = float(10 * np.log10((np.sum(a ** 2) + 1e-20) / (np.sum(b ** 2) + 1e-20)))
    c = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else 0.0
    return r, c


def transient_mask(x: np.ndarray, sr: int, jump_db: float = 10.0, before_s: float = 0.02,
                   after_s: float = 0.12) -> np.ndarray:
    """突発音 (机・拍手) の近傍 = True. 5 ms エネルギーが直前 10-60 ms の中央値より
    jump_db 以上跳ねた点を立ち上がりとし, その前 before_s / 後 after_s 秒 (残響込み)."""
    h = max(1, int(0.005 * sr))
    n = len(x) // h
    e = np.mean(x[: n * h].reshape(n, h) ** 2, axis=1) + 1e-12
    mask = np.zeros(len(x), bool)
    lo, hi = 12, 2  # 60 ms 前〜10 ms 前
    for i in range(lo, n):
        if e[i] > np.median(e[i - lo: i - hi]) * 10 ** (jump_db / 10):
            mask[max(0, int(i * h - before_s * sr)): int(i * h + after_s * sr)] = True
    return mask


def enhance_checked(x: np.ndarray, sr: int, conv: np.ndarray, context_s: float = 0.5,
                    fail_db: float = -6.0, fail_corr: float = 0.9) -> tuple[np.ndarray, list[dict]]:
    """発話ごとに切り出して SepFormer にかけ, 失敗を検出したら従来法で置き換える.

    SepFormer は長い区間をまとめて処理すると, 区間の内容によって一部の発話を
    復元できない (出力が入力とほぼ無相関, または形が崩れる) ことがある.
    実録音では 20 s 区切りで 3 発話, 4 s 区切りでも別の 3 発話で起き, 区切り方を
    変えると場所が移るだけだった. 一方, その発話だけを前後 context_s 秒付きで
    切り出すと全例で復元できたため, 最初から発話単位で処理する.
    発話区間の外は周期性ゲートで落ちるので DNN に通さない (出力 0).

    安全網: 有声フレームの 1 kHz 以下 (声の倍音が支配的で従来法でも確実に残る帯域)
    で, 従来法の出力 conv に対するエネルギー比が fail_db 未満, または相関が
    fail_corr 未満なら失敗とみなし, その発話は従来法の出力を使う.
    突発音の近傍は conv 側に物音が残っていて比較の基準にならないので除外する
    (除外しないと, DNN が物音を正しく消すほど相関が下がり誤って置き換えてしまう).
    """
    from scipy.signal import butter

    from .asr import active_intervals
    from .pitch import pitch_track

    sos = butter(4, 1000, "lp", fs=sr, output="sos")
    p = pitch_track(conv, sr)
    voiced = np.interp(np.arange(len(x)) / sr, p.t, p.voiced.astype(float),
                       left=0, right=0) > 0.5
    voiced &= ~transient_mask(x, sr)
    y = np.zeros(len(x))
    log = []
    f = int(0.02 * sr)
    for a, b in active_intervals(conv, sr):
        c0, c1 = max(0, int((a - context_s) * sr)), min(len(x), int((b + context_s) * sr))
        seg = enhance(x[c0:c1], sr)
        i0, i1 = int(a * sr), int(b * sr)
        r, c = _voiced_lowband_check(seg[i0 - c0: i1 - c0], conv[i0:i1], voiced[i0:i1], sos)
        ok = r >= fail_db and c >= fail_corr
        if not ok:
            seg = conv[c0:c1]
        # 前後の context 部分は 20 ms のフェードで重ねる (隣の発話と重なる場合は加算)
        w = np.ones(c1 - c0)
        ff = min(f, (c1 - c0) // 2)
        if ff > 0:
            w[:ff] = np.linspace(0, 1, ff)
            w[-ff:] = np.linspace(1, 0, ff)
        y[c0:c1] = np.where(y[c0:c1] != 0, 0.5 * (y[c0:c1] + seg * w), seg * w)
        log.append({"start": a, "end": b, "lowband_ratio_db": r, "lowband_corr": c,
                    "action": "dnn" if ok else "fallback"})
    return y, log
