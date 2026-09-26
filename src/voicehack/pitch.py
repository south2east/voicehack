"""トーン: 基本周波数 F0 (声の高さ) を YIN で推定.

参考文献
- A. de Cheveigné and H. Kawahara, "YIN, a fundamental frequency estimator for
  speech and music," JASA, 111(4), 1917-1930, 2002.
  Step 2 差分関数 / Step 3 累積平均正規化差分 (CMND) / Step 4 絶対閾値 /
  Step 5 放物線補間 を実装 (Step 6 best local estimate は中央値平滑で代替).
- D. Hirst, "The analysis by synthesis of speech melody: from data to models,"
  Journal of Speech Sciences, 1(1), 55-83, 2011.
  F0 の四分位から話者の声域を floor = 0.75·Q1, ceiling = 1.5·Q3 と決める.
- P. Boersma, "Accurate short-term analysis of the fundamental frequency and the
  harmonics-to-noise ratio of a sampled sound," Proc. IFA 17, 97-110, 1993.
  (オクターブ跳躍を嫌う経路探索. ここでは輪郭の連続性による断片選択に簡略化)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import median_filter


@dataclass
class PitchTrack:
    t: np.ndarray
    f0: np.ndarray          # Hz, 無声区間は NaN
    aperiodicity: np.ndarray  # CMND の最小値 (0 = 完全周期, 1 = 無周期)

    @property
    def voiced(self) -> np.ndarray:
        return ~np.isnan(self.f0)


def yin(x: np.ndarray, sr: int, fmin: float = 60.0, fmax: float = 500.0,
        threshold: float = 0.15, voicing_threshold: float = 0.35,
        hop_s: float = 0.01, win_s: float = 0.04, silence_db: float = -45.0) -> PitchTrack:
    """threshold: Step 4 の絶対閾値 (論文 0.1〜0.15).
    voicing_threshold: 閾値以下の谷が無いとき論文通り大域最小を採るが,
    その CMND 値がこれを超えるフレームは無声とする (実録音の弱い周期性への対応).
    既定値 (win 40 ms, 0.35) は実録音で Praat の有声判定と照合して決めた
    (再現率 0.73 / 適合率 0.92 / 1 半音超の誤り 5.7%)."""
    tau_min = max(2, int(sr / fmax))
    tau_max = int(np.ceil(sr / fmin))
    W = max(int(win_s * sr), tau_max)  # 積分窓 (最長周期以上)
    N = W + tau_max + 1
    hop = int(hop_s * sr)
    if len(x) < N:
        x = np.pad(x, (0, N - len(x)))
    frames = sliding_window_view(x, N)[::hop]          # (n_frames, N)
    t = (np.arange(len(frames)) * hop + W / 2) / sr

    # Step 2: d(τ) = E0 + Eτ - 2 r(τ)  (r は FFT による相互相関)
    L = 1 << int(np.ceil(np.log2(N + W)))
    Fa = np.fft.rfft(frames[:, :W], L)
    Fb = np.fft.rfft(frames, L)
    r = np.fft.irfft(np.conj(Fa) * Fb, L)[:, : tau_max + 1]
    c = np.concatenate([np.zeros((len(frames), 1)), np.cumsum(frames ** 2, axis=1)], axis=1)
    taus = np.arange(tau_max + 1)
    E0 = c[:, W][:, None]
    Et = c[:, taus + W] - c[:, taus]
    d = np.maximum(E0 + Et - 2.0 * r, 0.0)

    # Step 3: CMND d'(τ) = d(τ) / ((1/τ) Σ_{j=1..τ} d(j)),  d'(0) = 1
    cm = np.cumsum(d[:, 1:], axis=1)
    dn = np.ones_like(d)
    dn[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(cm, 1e-12)

    f0 = np.full(len(frames), np.nan)
    ap = np.ones(len(frames))
    energy_db = 10 * np.log10(np.maximum(E0[:, 0] / W, 1e-12)) + 3.01
    for i in range(len(frames)):
        row = dn[i]
        seg = row[tau_min: tau_max]
        # Step 4: 閾値を下回る最初の τ から局所最小へ下る
        below = np.nonzero(seg < threshold)[0]
        if len(below):
            tau = below[0] + tau_min
        else:
            tau = int(np.argmin(seg)) + tau_min
            if seg[tau - tau_min] > voicing_threshold:
                ap[i] = float(seg.min())
                continue
        while tau + 1 < tau_max and row[tau + 1] < row[tau]:
            tau += 1
        ap[i] = float(row[tau])
        # 探索範囲の端 (fmax/fmin に張り付く) は範囲外の周期音の折り返しとみなし棄却
        if energy_db[i] < silence_db or tau <= tau_min or tau >= tau_max - 1:
            continue
        # Step 5: 放物線補間
        a, b, cc = row[tau - 1], row[tau], row[tau + 1]
        den = a - 2 * b + cc
        shift = 0.5 * (a - cc) / den if den > 0 else 0.0
        f0[i] = sr / (tau + shift)

    # オクターブ誤りなどの孤立外れ値を 5 点中央値で抑制 (有声区間内のみ)
    f0 = _nan_median(f0, 5)
    return PitchTrack(t, f0, ap)


def pitch_track(x: np.ndarray, sr: int, fmin: float = 60.0, fmax: float = 700.0,
                max_jump_st: float = 3.0, **kw) -> PitchTrack:
    """話者の声域と輪郭の連続性で外れ値を除いた F0.

    1. 広い範囲 (fmin–fmax) で YIN.
    2. 声域を Hirst (2011) にならい floor = 0.75·Q1, ceiling = 1.5·Q3 とする.
    3. 有声区間を, 隣接フレーム間で max_jump_st 半音を超えて跳ぶ所で断片に分け,
       中央値が声域内の断片は丸ごと残し, 声域外の断片は捨てる.

    Hirst の原法は声域で探索範囲そのものを切るため, 声域の外まで滑らかに
    上がる声 (「あー」の上昇など) が上限で打ち切られていた. オクターブ誤りや
    物音は輪郭から跳び離れた断片になる (Boersma 1993 の経路探索が
    オクターブ跳躍にコストを課すのと同じ考え方) ので, 連続性で見分ける.
    実録音 9 本で Praat と比べ, 20% 超の誤り 4.9% → 4.3%,
    「あー」上昇の 250 Hz 超フレームの追跡 15/27 → 25/27.
    """
    p = yin(x, sr, fmin=fmin, fmax=fmax, **kw)
    f = p.f0[p.voiced]
    if len(f) < 10:
        return p
    q1, q3 = np.percentile(f, [25, 75])
    lo, hi = 0.75 * q1, 1.5 * q3
    f0 = p.f0.copy()
    for a, b in _continuous_pieces(f0, max_jump_st):
        if not lo <= np.median(f0[a:b]) <= hi:
            f0[a:b] = np.nan
    return PitchTrack(p.t, f0, p.aperiodicity)


def _continuous_pieces(f0: np.ndarray, max_jump_st: float) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(f0)
    while i < n:
        if np.isnan(f0[i]):
            i += 1
            continue
        j = i + 1
        while j < n and not np.isnan(f0[j]) and abs(12 * np.log2(f0[j] / f0[j - 1])) <= max_jump_st:
            j += 1
        out.append((i, j))
        i = j
    return out


def _nan_median(f0: np.ndarray, k: int) -> np.ndarray:
    v = ~np.isnan(f0)
    if v.sum() < k:
        return f0
    out = f0.copy()
    filled = np.where(v, f0, np.interp(np.arange(len(f0)), np.nonzero(v)[0], f0[v]))
    out[v] = median_filter(filled, size=k, mode="nearest")[v]
    return out


def hz_to_semitones(f: np.ndarray, ref: float = 55.0) -> np.ndarray:
    return 12.0 * np.log2(f / ref)


def pitch_summary(p: PitchTrack) -> dict:
    f = p.f0[p.voiced]
    if len(f) < 3:
        return {"voiced_ratio": float(p.voiced.mean()), "median_f0_hz": None}
    st = hz_to_semitones(f)
    return {
        "voiced_ratio": float(p.voiced.mean()),
        "median_f0_hz": float(np.median(f)),
        "mean_f0_hz": float(np.mean(f)),
        "f0_p5_hz": float(np.percentile(f, 5)),
        "f0_p95_hz": float(np.percentile(f, 95)),
        # 抑揚の大きさ: 半音単位の標準偏差と 5-95% レンジ
        "f0_std_semitones": float(np.std(st)),
        "f0_range_semitones": float(np.percentile(st, 95) - np.percentile(st, 5)),
        "note": _note_name(float(np.median(f))),
    }


def _note_name(f: float) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    m = int(round(69 + 12 * np.log2(f / 440.0)))
    return f"{names[m % 12]}{m // 12 - 1}"
