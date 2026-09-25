"""スピード: 音節核検出による発話速度 / 調音速度.

参考文献
- N. H. de Jong and T. Wempe, "Praat script to detect syllable nuclei and
  measure speech rate automatically," Behavior Research Methods, 41(2),
  385-390, 2009.
  (1) 強度 (dB) 包絡のピークを音節核候補とする
  (2) 無音閾値: 強度の 0.99 分位点 - 25 dB 未満のピークは捨てる
  (3) 直前の谷から mindip (2 dB) 以上立ち上がるピークのみ残す
  (4) 有声 (F0 が定義されている) フレーム上のピークのみ残す
  (5) 0.3 s 以上の無音をポーズとし, 発話時間から除外して調音速度を算出
- D. Wang and S. S. Narayanan, "Robust speech rate estimation for spontaneous
  speech," IEEE TASLP, 15(8), 2190-2201, 2007.
  サブバンド時間相関 + 上位 M 帯域の帯域間相関で音節の山を鋭くしてから数える.
  全帯域強度では早口で音節の谷が埋まり, 音節核を数え落とす問題への対策.
- N. Morgan and E. Fosler-Lussier, "Combining multiple estimators of speaking
  rate," Proc. ICASSP, 729-732, 1998.  (mrate)
  エネルギー包絡の変調スペクトルの 1 次モーメント. 音節を数えないため,
  音節境界の検出誤りの影響を受けにくい速度指標.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.signal import ShortTimeFFT, find_peaks
from scipy.signal.windows import gaussian

from .pitch import PitchTrack


@dataclass
class RateResult:
    t: np.ndarray
    intensity_db: np.ndarray
    threshold_db: float
    nuclei_t: np.ndarray
    pauses: list[tuple[float, float]]
    summary: dict


def intensity(x: np.ndarray, sr: int, hop_s: float = 0.01, min_pitch: float = 50.0):
    """Praat の Intensity 相当: 実効長 3.2/min_pitch の窓で二乗平均し dB 化.
    (Praat は Kaiser 窓; ここでは同程度の実効長をもつガウス窓で近似)"""
    n = int(3.2 / min_pitch * sr)
    w = gaussian(n, std=n / 6)
    w /= w.sum()
    ms = np.convolve(x ** 2, w, mode="same")
    hop = int(hop_s * sr)
    idx = np.arange(0, len(x), hop)
    return idx / sr, 10 * np.log10(np.maximum(ms[idx], 1e-12)) + 3.01


def speech_rate(x: np.ndarray, sr: int, pitch: PitchTrack, silence_db: float = -25.0,
                min_dip_db: float = 2.0, min_pause_s: float = 0.3) -> RateResult:
    t, I = intensity(x, sr)
    # de Jong & Wempe: 0.99 分位点 - 25 dB. 雑音下で閾値が雑音床に埋もれないよう
    # 雑音床 (10 パーセンタイル) + 6 dB を下限とする (本実装の追加)
    thr = float(max(np.percentile(I, 99) + silence_db, np.percentile(I, 10) + 6.0))

    idx, _ = find_peaks(I, height=thr)
    # mindip: 次のピークとの間の谷まで min_dip 以上下がるピークのみ残す (原スクリプト通り)
    kept: list[int] = []
    for k, i in enumerate(idx):
        nxt = idx[k + 1] if k + 1 < len(idx) else len(I) - 1
        if I[i] - I[i:nxt + 1].min() >= min_dip_db:
            kept.append(i)
    # 有声チェック: ピーク時刻 ±20 ms に有声フレームがあるか
    nuc = []
    for i in kept:
        m = np.abs(pitch.t - t[i]) <= 0.02
        if np.any(pitch.voiced[m]):
            nuc.append(t[i])
    nuc = np.array(nuc)

    # ポーズ = 閾値未満が min_pause 以上続く区間
    silent = I < thr
    pauses: list[tuple[float, float]] = []
    start = None
    for k, s in enumerate(np.append(silent, False)):
        if s and start is None:
            start = k
        elif not s and start is not None:
            if (k - start) * (t[1] - t[0]) >= min_pause_s:
                pauses.append((float(t[start]), float(t[k - 1])))
            start = None

    total = len(x) / sr
    n = len(nuc)
    # 発話区間 = 最初の音節核 〜 最後の音節核 + 平均音節間隔 1 つ分.
    # (原論文は録音全長で割るが, 前後の無音や残留雑音に左右されないようにする)
    if n >= 2:
        isi = np.diff(nuc)
        a0, a1 = nuc[0] - np.median(isi) / 2, nuc[-1] + np.median(isi) / 2
        span = float(a1 - a0)
        inner = [(a, b) for a, b in pauses if a > a0 and b < a1]
        phon = max(span - sum(b - a for a, b in inner), 1e-6)
    else:
        span, inner, phon = total, [], total
    summary = {
        "syllables": n,
        "speech_rate_syll_per_s": n / span if n else 0.0,
        "articulation_rate_syll_per_s": n / phon if n else 0.0,
        "speech_rate_total_syll_per_s": n / total,  # 原論文の定義 (録音全長)
        "phonation_time_s": phon,
        "speaking_span_s": span,
        "n_pauses": len(inner),
        "avg_syllable_duration_s": phon / n if n else None,
    }
    return RateResult(t, I, thr, nuc, pauses, summary)


# --------------------------------------------------------------------------- #
# Wang & Narayanan (2007): サブバンド相関による音節核検出
# --------------------------------------------------------------------------- #
def _band_energies(x: np.ndarray, sr: int, n_bands: int, hop_s: float, win_s: float):
    n = int(win_s * sr)
    sft = ShortTimeFFT(np.hanning(n), int(hop_s * sr), sr, mfft=1 << int(np.ceil(np.log2(n))))
    P = np.abs(sft.stft(x)) ** 2
    t = sft.t(len(x))
    hz2mel = lambda f: 2595 * np.log10(1 + f / 700)
    mel2hz = lambda m: 700 * (10 ** (m / 2595) - 1)
    edges = mel2hz(np.linspace(hz2mel(100), hz2mel(min(sr / 2, 8000)), n_bands + 1))
    E = np.stack([P[(sft.f >= lo) & (sft.f < hi)].sum(axis=0) for lo, hi in zip(edges[:-1], edges[1:])])
    return t, E


def _pair_mean(sum_v, sum_v2, n):
    """Σ_{i<j} v_i v_j / (n(n-1)/2) を和と二乗和から計算."""
    return (sum_v ** 2 - sum_v2) / (n * (n - 1))


def subband_envelope(x: np.ndarray, sr: int, n_bands: int = 19, top_m: int = 12, k: int = 3,
                     hop_s: float = 0.01, win_s: float = 0.02, sigma: float = 1.5):
    """Wang & Narayanan (2007) の音節性包絡 [dB]."""
    t, E = _band_energies(x, sr, n_bands, hop_s, win_s)
    E = E / (E.max() + 1e-20)
    # (1) 各帯域の時間相関: 中心 K フレーム内の全ペア積の平均
    s1 = uniform_filter1d(E, k, axis=1) * k
    s2 = uniform_filter1d(E ** 2, k, axis=1) * k
    Y = np.maximum(_pair_mean(s1, s2, k), 0.0)
    # (2) フレーム毎にエネルギー上位 M 帯域を選び帯域間相関
    top = -np.sort(-Y, axis=0)[:top_m]
    z = np.maximum(_pair_mean(top.sum(axis=0), (top ** 2).sum(axis=0), top_m), 0.0)
    # (3) 4 次の積なので 1/4 乗でエネルギー尺度に戻し, ガウス平滑して dB 化
    env = gaussian_filter1d(z ** 0.25, sigma)
    return t, 10 * np.log10(env + 1e-12)


def subband_rate(x: np.ndarray, sr: int, pitch: PitchTrack, min_prom_db: float = 1.5,
                 min_dist_s: float = 0.05, silence_db: float = -25.0) -> dict:
    """K=3, σ=1.5 フレーム, prominence 1.5 dB は, 同じ文を速さを変えて読んだ
    音声 5 本 (TTS 3 速度 + 実録音 2 速度) で「速さの比」の誤差が最小になるよう選んだ."""
    t, env = subband_envelope(x, sr)
    thr = max(np.percentile(env, 99) + silence_db, np.percentile(env, 10) + 6.0)
    idx, _ = find_peaks(env, height=thr, prominence=min_prom_db,
                        distance=max(1, int(min_dist_s / (t[1] - t[0]))))
    nuc = np.array([t[i] for i in idx
                    if np.any(pitch.voiced[np.abs(pitch.t - t[i]) <= 0.03])])
    n = len(nuc)
    if n >= 2:
        isi = np.diff(nuc)
        span = float(nuc[-1] - nuc[0] + np.median(isi))
    else:
        span = len(x) / sr
    return {"t": t, "envelope_db": env, "threshold_db": float(thr), "nuclei_t": nuc,
            "syllables": n,
            "speech_rate_syll_per_s": n / span if n else 0.0, "speaking_span_s": span}


# --------------------------------------------------------------------------- #
# Morgan & Fosler-Lussier (1998): 変調スペクトルの 1 次モーメント (mrate)
# --------------------------------------------------------------------------- #
def modulation_rate(x: np.ndarray, sr: int, active: np.ndarray | None = None,
                    hop_s: float = 0.01, fmin: float = 1.0, fmax: float = 16.0,
                    win_s: float = 2.0) -> float | None:
    """発話区間のエネルギー包絡 (300-4000 Hz 帯) の変調スペクトル重心 [Hz].

    active: 包絡と同じ格子 (hop_s) の発話区間マスク. None なら全体を使う.
    発話が win_s より短ければ全体を 1 窓で評価する.
    """
    n = int(0.02 * sr)
    sft = ShortTimeFFT(np.hanning(n), int(hop_s * sr), sr, mfft=1 << int(np.ceil(np.log2(n))))
    P = np.abs(sft.stft(x)) ** 2
    band = (sft.f >= 300) & (sft.f <= 4000)
    env = np.sqrt(P[band].sum(axis=0))
    if active is not None:
        m = np.interp(np.arange(len(env)), np.linspace(0, len(env) - 1, len(active)),
                      active.astype(float)) > 0.5
        if m.sum() < int(0.5 / hop_s):
            return None
        idx = np.nonzero(m)[0]
        env = env[idx[0]: idx[-1] + 1]
    fs = 1 / hop_s
    L = min(len(env), int(win_s * fs))
    cents, weights = [], []
    for s0 in range(0, len(env) - L + 1, max(1, L // 4)):
        seg = env[s0:s0 + L]
        seg = (seg - seg.mean()) * np.hanning(L)
        spec = np.abs(np.fft.rfft(seg, 1 << int(np.ceil(np.log2(L * 4)))))
        f = np.fft.rfftfreq(1 << int(np.ceil(np.log2(L * 4))), 1 / fs)
        b = (f >= fmin) & (f <= fmax)
        cents.append(np.sum(f[b] * spec[b]) / (np.sum(spec[b]) + 1e-20))
        weights.append(np.sum(seg ** 2))
    return float(np.average(cents, weights=weights)) if cents else None


def rate_summary(dj: RateResult, wn: dict, mrate: float | None) -> dict:
    """主指標は Wang & Narayanan の音節数. ポーズは de Jong & Wempe の無音区間を使う."""
    n, nuc = wn["syllables"], wn["nuclei_t"]
    if n >= 2:
        half = float(np.median(np.diff(nuc))) / 2
        a0, a1 = nuc[0] - half, nuc[-1] + half
        inner = [(a, b) for a, b in dj.pauses if a > a0 and b < a1]
        phon = max(a1 - a0 - sum(b - a for a, b in inner), 1e-6)
    else:
        inner, phon = [], dj.summary["phonation_time_s"]
    return {
        "method": "Wang & Narayanan 2007 (subband correlation)",
        "syllables": n,
        "speech_rate_syll_per_s": wn["speech_rate_syll_per_s"],
        "articulation_rate_syll_per_s": n / phon if n else 0.0,
        "speaking_span_s": wn["speaking_span_s"],
        "phonation_time_s": phon,
        "n_pauses": len(inner),
        "modulation_rate_hz": mrate,
        "de_jong_2009": dj.summary,
    }
