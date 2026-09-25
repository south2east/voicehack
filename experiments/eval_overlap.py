"""声と重なった突発音の分離評価.

きれいな TTS 音声に, 実録音から切り出した机の音・拍手を声と重なる位置 (と
声の無い位置) に混ぜ, 部屋の雑音も加える. 正解の声が分かるので
  - SI-SDR (Le Roux et al., ICASSP 2019) の改善量
  - 突発音区間での漏れ = 推定音声の誤差エネルギー / 突発音エネルギー
を, 従来法 (MCRA + OM-LSA + 周期性ゲート) と DNN (SepFormer) で比べる.

  uv run python experiments/eval_overlap.py      (要: samples/make_samples.py, out/ の録音)
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

from voicehack.audio_io import load
from voicehack.denoise import separate
from voicehack.dnn import enhance, enhance_checked, gate_by_voice_region
from voicehack.loudness import rms_db

SR = 16000
# 実録音の突発音 (ピーク時刻): greetings の机 / test_clap の拍手
EVENTS = [("out/greetings/input.wav", t) for t in (1.52, 3.93, 5.97, 7.89)] + \
         [("out/test_clap/input.wav", t) for t in (3.34, 3.81, 4.27)]


def si_sdr(ref, est):
    a = np.dot(est, ref) / np.dot(ref, ref)
    return 10 * np.log10(np.sum((a * ref) ** 2) / np.sum((a * ref - est) ** 2))


def build(seed=0):
    rng = np.random.default_rng(seed)
    s = load("samples/normal_clean.wav").x
    # 部屋の雑音: protocol の無音部 (24-30 s) を繰り返す
    room, _ = sf.read("out/protocol/input.wav")
    room = room[24 * SR: 30 * SR]
    noise = np.resize(room, len(s))
    # 声のある時刻 (RMS) を探し, そこに突発音を置く
    t, r = rms_db(s, SR)
    speech_t = t[r > r.max() - 20]
    events = np.zeros(len(s))
    windows = []
    for k, (path, tp) in enumerate(EVENTS):
        x, _ = sf.read(path)
        i = int(tp * SR)
        ev = x[i - int(0.02 * SR): i + int(0.2 * SR)]
        ev = ev / np.max(np.abs(ev)) * 0.5 * np.max(np.abs(s))  # 声のピークの半分
        overlap = k < 5                                            # 5 個は声と重ねる
        pool = speech_t if overlap else t[r < r.max() - 40]
        c = int(rng.choice(pool) * SR)
        c = min(max(c, 0), len(s) - len(ev))
        events[c: c + len(ev)] += ev
        windows.append((c, c + int(0.06 * SR), overlap))
    return s, noise, events, windows


def evaluate(name, s, y, ev, windows):
    sdr = si_sdr(s, y)
    leak = {True: [], False: []}
    for a, b, ov in windows:
        err = np.sum((y[a:b] - s[a:b]) ** 2)
        leak[ov].append(err / np.sum(ev[a:b] ** 2))
    f = lambda v: max(-99.0, 10 * np.log10(np.mean(v) + 1e-12))
    print(f"  {name:10s} SI-SDR {sdr:6.2f} dB | 漏れ(重なり) {f(leak[True]):6.1f} dB | "
          f"漏れ(単独) {f(leak[False]):6.1f} dB")


def main():
    for seed in (0, 1, 2):
        s, noise, ev, win = build(seed)
        x = s + noise + ev
        print(f"seed {seed}:")
        evaluate("入力", s, x, ev, win)
        evaluate("従来法", s, separate(x, SR).speech, ev, win)
        sep = separate(x, SR)
        y = enhance(x, SR, chunk_s=20.0)
        evaluate("DNN(20s)", s, gate_by_voice_region(x, y, SR, sep=sep), ev, win)
        yc, _ = enhance_checked(x, SR, sep.speech)
        evaluate("最終", s, gate_by_voice_region(x, yc, SR, sep=sep), ev, win)


if __name__ == "__main__":
    main()
