"""大きさ: RMS レベルと ITU-R BS.1770 ラウドネス (LUFS).

参考文献
- ITU-R BS.1770-4, "Algorithms to measure audio programme loudness and
  true-peak audio level," 2015.  -> K 特性フィルタ, 400 ms ブロック, 2 段ゲート
- EBU Tech 3341 (2016) -> Momentary (400 ms) / Short-term (3 s) ラウドネス
- EBU Tech 3342 (2016) -> Loudness Range (LRA)
- K 特性フィルタの任意サンプリング周波数への拡張は, 48 kHz 規格係数を
  双一次変換のアナログ原型へ逆算した B. De Man の導出 (pyloudnorm と同じ) を使用.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .spectral import EPS

# 48 kHz 係数から逆算されたアナログ原型パラメータ
_SHELF = dict(fc=1681.974450955533, G=3.999843853973347, Q=0.7071752369554196)
_HPF = dict(fc=38.13547087602444, Q=0.5003270373238773)


def k_weighting_coeffs(sr: int) -> list[tuple[np.ndarray, np.ndarray]]:
    # stage 1: 高域シェルフ (頭部の音響効果のモデル)
    K = np.tan(np.pi * _SHELF["fc"] / sr)
    Q = _SHELF["Q"]
    Vh = 10.0 ** (_SHELF["G"] / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b1 = np.array([Vh + Vb * K / Q + K * K, 2.0 * (K * K - Vh), Vh - Vb * K / Q + K * K]) / a0
    a1 = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])
    # stage 2: RLB 高域通過
    K = np.tan(np.pi * _HPF["fc"] / sr)
    Q = _HPF["Q"]
    a0 = 1.0 + K / Q + K * K
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])
    return [(b1, a1), (b2, a2)]


def k_weight(x: np.ndarray, sr: int) -> np.ndarray:
    for b, a in k_weighting_coeffs(sr):
        x = lfilter(b, a, x)
    return x


def _block_ms(z: np.ndarray, sr: int, win_s: float, hop_s: float) -> tuple[np.ndarray, np.ndarray]:
    """二乗平均 z を窓長 win_s / 間隔 hop_s で計算 (累積和で O(N))."""
    n, h = int(round(win_s * sr)), int(round(hop_s * sr))
    c = np.concatenate([[0.0], np.cumsum(z)])
    if len(z) < n:
        return np.array([c[-1] / max(len(z), 1)]), np.array([len(z) / sr / 2])
    starts = np.arange(0, len(z) - n + 1, h)
    ms = (c[starts + n] - c[starts]) / n
    return ms, (starts + n) / sr  # 時刻は窓の終端 (EBU 3341 の実時間メータ流儀)


def _lufs(ms: np.ndarray | float) -> np.ndarray:
    return -0.691 + 10.0 * np.log10(np.maximum(ms, EPS))


def loudness(x: np.ndarray, sr: int) -> dict:
    y = k_weight(x, sr)
    z = y ** 2

    # Integrated (BS.1770-4 §2): 400 ms / 75% overlap, 絶対ゲート -70, 相対ゲート -10 LU
    ms, _ = _block_ms(z, sr, 0.4, 0.1)
    lj = _lufs(ms)
    g1 = ms[lj > -70.0]
    if len(g1):
        rel = _lufs(g1.mean()) - 10.0
        g2 = ms[(lj > -70.0) & (lj > rel)]
        integrated = float(_lufs(g2.mean())) if len(g2) else -np.inf
    else:
        integrated = -np.inf

    # Momentary / Short-term (EBU 3341)
    m_ms, m_t = _block_ms(z, sr, 0.4, 0.1)
    s_ms, s_t = _block_ms(z, sr, 3.0, 0.1)
    st = _lufs(s_ms)

    # LRA (EBU 3342): short-term, 絶対 -70, 相対 -20 LU, P95 - P10
    st_g = st[st > -70.0]
    if len(st_g) >= 2:
        rel = _lufs(np.mean(10 ** ((st_g + 0.691) / 10))) - 20.0
        st_g = st_g[st_g > rel]
        lra = float(np.percentile(st_g, 95) - np.percentile(st_g, 10)) if len(st_g) >= 2 else 0.0
    else:
        lra = 0.0

    return {
        "integrated_lufs": integrated,
        "loudness_range_lu": lra,
        "max_momentary_lufs": float(_lufs(m_ms).max()),
        "momentary": (m_t, _lufs(m_ms)),
        "short_term": (s_t, st),
    }


def rms_db(x: np.ndarray, sr: int, win_s: float = 0.03, hop_s: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """フレーム RMS [dBFS] (正弦波振幅 1 を 0 dBFS とする: +3.01 dB 補正)."""
    ms, t = _block_ms(x ** 2, sr, win_s, hop_s)
    return t - win_s / 2, 10 * np.log10(np.maximum(ms, EPS)) + 3.0103


def level_summary(x: np.ndarray) -> dict:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    return {
        "peak_dbfs": float(20 * np.log10(max(peak, EPS))),
        "rms_dbfs": float(20 * np.log10(max(rms, EPS)) + 3.0103),
        "crest_factor_db": float(20 * np.log10(max(peak, EPS) / max(rms, EPS))),
    }
