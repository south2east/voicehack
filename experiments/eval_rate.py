"""発話速度推定の評価: 同じ文を 普通/ゆっくり/早口 で読んだ録音で, 速さの比を比べる.

正解の速さ = モーラ数 / 発話区間の長さ. 推定器は音節を数えるので絶対値は合わないが,
同じ文どうしの比は一致するはず. dev でパラメータを選び, test で 1 回だけ評価する.

  uv run python experiments/eval_rate.py            # dev でグリッド探索 → test 評価
録音は out/ 以下 (git 管理外). samples/make_samples.py で TTS も作っておくこと.
"""

from __future__ import annotations

import itertools

import numpy as np
import soundfile as sf
from scipy.signal import ShortTimeFFT, find_peaks

from voicehack.audio_io import load
from voicehack.denoise import separate
from voicehack.pitch import pitch_track
from voicehack.rate import subband_envelope

SR = 16000
# (名前, ファイル, モーラ数, {速度: (開始, 終了)})  区間は RMS による自動検出値
SETS = {
    "dev": [
        ("protocol", "out/protocol/input.wav", 10, {"slow": (4.4, 6.3), "fast": (7.7, 9.0)}),
        ("set1", "out/rate_set1/input.wav", 17,
         {"normal": (2.01, 3.94), "slow": (6.77, 10.26), "fast": (13.84, 15.18)}),
        ("set2", "out/rate_set2/input.wav", 18,
         {"normal": (2.09, 4.33), "slow": (6.90, 10.09), "fast": (12.48, 14.15)}),
    ],
    "test": [
        ("heldout", "out/heldout_rate/input.wav", 23,
         {"normal": (2.19, 4.37), "slow": (6.41, 10.76), "fast": (12.30, 13.93)}),
        ("set4", "out/rate_set4/input.wav", 19,
         {"normal": (3.10, 5.74), "slow": (8.14, 12.29), "fast": (14.71, 16.36)}),
    ],
}
# set3 (私は毎朝コーヒーを飲みます) は「ゆっくり」を途中まで早口で読んだため除外
# (録音者の申告による. 評価結果とは無関係に決定)
TTS = [("tts", {s: f"samples/{s}_noisy.wav" for s in ("slow", "normal", "fast")},
        {"slow": 9.85, "normal": 8.32, "fast": 5.96})]


def load_utterances():
    """split -> list of (set名, {速度: (音声, 正解の相対速度)})"""
    out = {"dev": [], "test": []}
    for split, items in SETS.items():
        for name, path, mora, segs in items:
            x, _ = sf.read(path)
            sp = separate(x, SR).speech
            utt = {}
            for spd, (a, b) in segs.items():
                sl = slice(int((a - 0.3) * SR), int((b + 0.3) * SR))
                utt[spd] = (sp[sl], mora / (b - a), x[sl])
            out[split].append((name, utt))
    for name, files, dur in TTS:
        out["dev"].append((name, {s: (separate(load(f).x, SR).speech, 1.0 / dur[s], load(f).x)
                                  for s, f in files.items()}))
    return out


def _lowratio(x):
    sft = ShortTimeFFT(np.hanning(400), 160, SR, mfft=512)
    P = np.abs(sft.stft(x)) ** 2
    lo = P[(sft.f >= 100) & (sft.f < 1000)].sum(0)
    return sft.t(len(x)), lo / (P[sft.f >= 100].sum(0) + 1e-20)


def estimate(x, pitch, k=3, sigma=1.5, prom=1.5, voicing="yin30"):
    t, env = subband_envelope(x, SR, k=k, sigma=sigma)
    thr = max(np.percentile(env, 99) - 25, np.percentile(env, 10) + 6)
    idx, _ = find_peaks(env, height=thr, prominence=prom, distance=5)
    lt, lr = _lowratio(x)
    keep = []
    for i in idx:
        ti = t[i]
        if voicing == "yin30":
            ok = np.any(pitch.voiced[np.abs(pitch.t - ti) <= 0.03])
        elif voicing == "yin50":
            ok = np.any(pitch.voiced[np.abs(pitch.t - ti) <= 0.05])
        elif voicing == "lowband":
            ok = lr[np.argmin(np.abs(lt - ti))] > 0.5
        else:
            ok = True
        if ok:
            keep.append(ti)
    nuc = np.array(keep)
    if len(nuc) < 2:
        return 0.0
    return len(nuc) / (nuc[-1] - nuc[0] + np.median(np.diff(nuc)))


def ratio_errors(utts, pitches, **kw):
    errs = []
    for (name, utt), P in zip(utts, pitches):
        est = {s: estimate(utt[s][0], P[s], **kw) for s in utt}
        for a, b in itertools.combinations(sorted(utt), 2):
            if est[b] <= 0:
                errs.append(1.0)
                continue
            true = utt[a][1] / utt[b][1]
            errs.append(abs((est[a] / est[b]) / true - 1))
    return np.array(errs)


def asr_ratio_errors(utts):
    """音声認識によるモーラ速度 (パラメータ無し: dev/test とも純粋な評価)."""
    from voicehack.asr import mora_rate

    errs, abs_err = [], []
    for name, utt in utts:
        est = {}
        for s, (x, true, raw) in utt.items():
            r = mora_rate(x, SR, asr_input=raw)
            est[s] = r["speech_rate_mora_per_s"] or 0.0
            if name != "tts":
                abs_err.append(abs(est[s] / true - 1))
            print(f"    {name:8s} {s:6s} {r['morae']:2d} モーラ "
                  f"{' / '.join(u['text'] for u in r['utterances'])}  "
                  f"推定 {est[s]:.1f} (正解 {true:.1f})" if name != "tts" else
                  f"    {name:8s} {s:6s} {r['morae']:2d} モーラ 推定 {est[s]:.1f}/s")
        for a, b in itertools.combinations(sorted(utt), 2):
            true = utt[a][1] / utt[b][1]
            errs.append(abs((est[a] / est[b]) / true - 1) if est[b] > 0 else 1.0)
    return np.array(errs), np.array(abs_err)


def main():
    data = load_utterances()
    pitches = {sp: [{s: pitch_track(v[0], SR) for s, v in utt.items()} for _, utt in data[sp]]
               for sp in data}
    base = dict(k=3, sigma=1.5, prom=1.5, voicing="yin30")
    grid = [dict(k=k, sigma=sg, prom=pr, voicing=v)
            for k in (3, 5) for sg in (1.0, 1.5, 2.0) for pr in (0.5, 1.0, 1.5, 2.0)
            for v in ("yin30", "yin50", "lowband", "none")]
    scored = sorted(((ratio_errors(data["dev"], pitches["dev"], **g).mean(), g) for g in grid),
                    key=lambda z: z[0])
    print("dev 上位 5:")
    for m, g in scored[:5]:
        print(f"  {m:.3f}  {g}")
    best = scored[0][1]
    for label, g in (("現行", base), ("dev 最良", best)):
        d = ratio_errors(data["dev"], pitches["dev"], **g)
        t = ratio_errors(data["test"], pitches["test"], **g)
        print(f"{label:6s} dev 平均誤差 {d.mean():.3f} | test 平均誤差 {t.mean():.3f} "
              f"(中央値 {np.median(t):.3f}, 組数 {len(t)})  {g}")
    for split in ("dev", "test"):
        print(f"ASR ({split}):")
        e, a = asr_ratio_errors(data[split])
        print(f"ASR    {split} 比の平均誤差 {e.mean():.3f} (中央値 {np.median(e):.3f}, 組数 {len(e)})"
              f" | モーラ/s 絶対値の平均誤差 {a.mean():.3f}")


if __name__ == "__main__":
    main()
