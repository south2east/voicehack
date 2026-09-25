"""ノイズ分離: 音声 (speech) と環境音 (environment) への分解.

1. 雑音パワースペクトル推定: MCRA
   I. Cohen and B. Berdugo, "Noise estimation by minima controlled recursive
   averaging for robust speech enhancement," IEEE SPL, 9(1), 12-15, 2002.
2. 音声スペクトル振幅推定: MMSE log-spectral amplitude (LSA) 推定器
   Y. Ephraim and D. Malah, "Speech enhancement using a minimum mean-square error
   log-spectral amplitude estimator," IEEE TASSP, 33(2), 443-445, 1985.
3. 事前 SNR: decision-directed 法
   Y. Ephraim and D. Malah, "... short-time spectral amplitude estimator,"
   IEEE TASSP, 32(6), 1109-1121, 1984.  (ξ_min は O. Cappé, IEEE TSAP 1994 の
   musical noise 解析に基づき -25 dB)
4. OM-LSA の音声存在確率によるゲート
   I. Cohen and B. Berdugo, "Speech enhancement for non-stationary noise
   environments," Signal Processing, 81(11), 2403-2418, 2001.

環境音は STFT 領域の残差 E = Y - Ŝ = (1 - G) Y として得る. よって
speech + environment = 元信号 (STFT の完全再構成の範囲で) が成り立つ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.special import exp1

from .spectral import STFT, stft


@dataclass
class Separation:
    speech: np.ndarray
    environment: np.ndarray
    gain: np.ndarray           # (freq, frame) 音声側ゲート G
    noise_psd: np.ndarray      # (freq, frame) λ_d
    speech_prob: np.ndarray    # (freq, frame) MCRA の音声存在確率 p
    S: STFT


def mcra(P: np.ndarray, hop_s: float, alpha_s: float = 0.8, alpha_p: float = 0.2,
         alpha_d: float = 0.95, delta: float = 5.0, window_s: float = 1.0,
         init_s: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """MCRA 雑音推定 (Cohen & Berdugo 2002, 式 (5)-(13))."""
    n_f, n_t = P.shape
    L = max(2, int(round(window_s / hop_s)))  # 最小値探索窓 (~1 s)
    # 周波数方向平滑 b = [0.25, 0.5, 0.25] (w = 1)
    Pf = P.copy()
    Pf[1:-1] = 0.25 * P[:-2] + 0.5 * P[1:-1] + 0.25 * P[2:]

    # 初期値: 先頭 init_s 秒を雑音のみと仮定 (STFT 先頭のゼロ詰めフレームで
    # Smin が過小になり p≈1 に張り付くのを防ぐため S/Smin も同じ区間平均で初期化)
    n_init = max(1, min(n_t, int(init_s / hop_s)))
    lam = P[:, :n_init].mean(axis=1)
    S = Pf[:, :n_init].mean(axis=1)
    Smin = S.copy()
    Stmp = S.copy()
    p = np.zeros(n_f)
    lam_out = np.empty_like(P)
    p_out = np.empty_like(P)
    for l in range(n_t):
        S = alpha_s * S + (1 - alpha_s) * Pf[:, l]
        Smin = np.minimum(Smin, S)
        Stmp = np.minimum(Stmp, S)
        if (l + 1) % L == 0:
            Smin = np.minimum(Stmp, S)
            Stmp = S.copy()
        I = (S / np.maximum(Smin, 1e-20)) > delta
        p = alpha_p * p + (1 - alpha_p) * I
        a = alpha_d + (1 - alpha_d) * p
        lam = a * lam + (1 - a) * P[:, l]
        lam_out[:, l] = lam
        p_out[:, l] = p
    return lam_out, p_out


def mmse_lsa(P: np.ndarray, lam: np.ndarray, alpha: float = 0.98,
             xi_min_db: float = -25.0) -> np.ndarray:
    """decision-directed ξ + LSA ゲイン G = ξ/(1+ξ) · exp(½ E1(v))."""
    xi_min = 10 ** (xi_min_db / 10)
    gamma = P / np.maximum(lam, 1e-20)
    G = np.empty_like(P)
    g_prev = np.ones(P.shape[0])
    gam_prev = np.ones(P.shape[0])
    for l in range(P.shape[1]):
        xi = alpha * g_prev ** 2 * gam_prev + (1 - alpha) * np.maximum(gamma[:, l] - 1, 0)
        xi = np.maximum(xi, xi_min)
        v = np.maximum(xi * gamma[:, l] / (1 + xi), 1e-10)
        g = xi / (1 + xi) * np.exp(0.5 * exp1(v))
        g = np.minimum(g, 1.0)
        G[:, l] = g
        g_prev, gam_prev = g, gamma[:, l]
    return G


def separate(x: np.ndarray, sr: int, g_min_db: float = -25.0,
             presence_gate: bool = True) -> Separation:
    S = stft(x, sr, win_s=0.032, hop_ratio=0.25)
    hop_s = S.sft.hop / sr
    P = S.power
    lam, p = mcra(P, hop_s)
    G = mmse_lsa(P, lam)
    if presence_gate:
        # OM-LSA (Cohen & Berdugo 2001): G = G_H1^p · G_min^(1-p)
        # p は時間方向に軽く平滑 (≈50 ms) してから使う
        ps = uniform_filter1d(p, size=max(1, int(0.05 / hop_s)), axis=1)
        gmin = 10 ** (g_min_db / 20)
        G = np.maximum(G, gmin) ** ps * gmin ** (1 - ps)
    speech = S.inverse(G * S.X)
    env = S.inverse((1 - G) * S.X)
    return Separation(speech, env, G, lam, p, S)
