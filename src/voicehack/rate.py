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
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks
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
