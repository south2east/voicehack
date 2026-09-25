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
5. 周期性 (有声性) に基づく音声区間ゲート
   R. Tucker, "Voice activity detection using a periodicity measure,"
   IEE Proceedings-I, 139(4), 377-380, 1992.
   MCRA は定常雑音しか雑音とみなさないため, 机を叩く音・拍手などの突発音は
   「音声」側に残る. そこで音声推定から YIN で有声区間を求め, その前後
   のうち音声存在確率が途切れずにつながる区間 (子音・無声化母音を含む) の外では
   ゲインを G_min に落とす.
   声と時間的に重なった突発音はこの方法では分けられない.

環境音は STFT 領域の残差 E = Y - Ŝ = (1 - G) Y として得る. よって
speech + environment = 元信号 (STFT の完全再構成の範囲で) が成り立つ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.special import exp1

from .pitch import yin_two_pass
from .spectral import STFT, stft


@dataclass
class Separation:
    speech: np.ndarray
    environment: np.ndarray
    gain: np.ndarray           # (freq, frame) 音声側ゲート G
    noise_psd: np.ndarray      # (freq, frame) λ_d
    speech_prob: np.ndarray    # (freq, frame) MCRA の音声存在確率 p
    S: STFT
    voice_region: np.ndarray | None = None  # (frame,) 周期性ゲート [0, 1]


def mcra(P: np.ndarray, hop_s: float, alpha_s: float = 0.8, alpha_p: float = 0.2,
         alpha_d: float = 0.95, delta: float = 5.0, window_s: float = 1.0,
         init_q: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """MCRA 雑音推定 (Cohen & Berdugo 2002, 式 (5)-(13))."""
    n_f, n_t = P.shape
    L = max(2, int(round(window_s / hop_s)))  # 最小値探索窓 (~1 s)

    # 初期値: 全体のうちフレームエネルギー下位 init_q の区間を雑音のみとみなす
    # (原論文の「先頭は雑音のみ」の仮定は, 冒頭から話し始めると崩れるため).
    e = P.sum(axis=0)
    ok = e > np.median(e) * 1e-4          # デジタル無音 (マイク起動直後・ドロップアウト)
    cand = np.nonzero(ok)[0] if ok.any() else np.arange(n_t)
    init = cand[e[cand] <= np.quantile(e[cand], init_q)]
    lam = P[:, init].mean(axis=1)
    # デジタル無音フレームは欠損扱いで初期雑音に置換: そのまま通すと S が急落し,
    # Smin が最大 2 窓 (≈2 s) 過小なまま残って p≈1 に張り付く
    P = np.where(ok[None, :], P, lam[:, None])

    # 周波数方向平滑 b = [0.25, 0.5, 0.25] (w = 1)
    Pf = P.copy()
    Pf[1:-1] = 0.25 * P[:-2] + 0.5 * P[1:-1] + 0.25 * P[2:]
    S = Pf[:, init].mean(axis=1)
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


def voice_region(x: np.ndarray, sr: int, t_frames: np.ndarray, presence: np.ndarray,
                 pad_s: float = 0.1, min_voiced_s: float = 0.05, active_thr: float = 0.5,
                 ramp_s: float = 0.03) -> np.ndarray:
    """「声の区間」を STFT フレーム上で [0, 1] で返す.

    有声区間 (YIN, min_voiced_s 未満の孤立判定は机の共鳴などとして除去) を核に,
    MCRA の音声存在確率 (帯域平均) が active_thr を超えて途切れずにつながる区間
    全体を声とみなす. 子音や語末の無声化母音 (「です」の「す」) は母音と
    つながっているので残り, 無音を挟んだ突発音は外れる. pad_s は安全余白.
    """
    p = yin_two_pass(x, sr)
    v = p.voiced.copy()
    hop = float(p.t[1] - p.t[0]) if len(p.t) > 1 else 0.01
    for a, b in _runs(v):
        if (b - a) * hop < min_voiced_s:
            v[a:b] = False
    # フレーム格子を STFT に合わせる
    vf = np.interp(t_frames, p.t, v.astype(float), left=0.0, right=0.0) > 0.5
    fhop = float(t_frames[1] - t_frames[0]) if len(t_frames) > 1 else hop
    pad = int(round(pad_s / fhop))
    cs = np.concatenate([[0], np.cumsum(vf)])
    idx = np.arange(len(vf))
    region = (cs[np.minimum(len(vf), idx + pad + 1)] - cs[np.maximum(0, idx - pad)]) > 0
    active = presence > active_thr
    for a, b in _runs(active):
        if vf[a:b].any():
            region[a:b] = True
    return uniform_filter1d(region.astype(float), size=max(1, int(round(ramp_s / fhop))))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    e = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.nonzero(e == 1)[0], np.nonzero(e == -1)[0]))


def separate(x: np.ndarray, sr: int, g_min_db: float = -25.0,
             presence_gate: bool = True, voicing_gate: bool = True) -> Separation:
    S = stft(x, sr, win_s=0.032, hop_ratio=0.25)
    hop_s = S.sft.hop / sr
    P = S.power
    lam, p = mcra(P, hop_s)
    G = mmse_lsa(P, lam)
    gmin = 10 ** (g_min_db / 20)
    if presence_gate:
        # OM-LSA (Cohen & Berdugo 2001): G = G_H1^p · G_min^(1-p)
        # p は時間方向に軽く平滑 (≈50 ms) してから使う
        ps = uniform_filter1d(p, size=max(1, int(0.05 / hop_s)), axis=1)
        G = np.maximum(G, gmin) ** ps * gmin ** (1 - ps)
    else:
        ps = uniform_filter1d(p, size=max(1, int(0.05 / hop_s)), axis=1)
    region = None
    if voicing_gate:
        # 周期性ゲート (Tucker 1992): 声の区間の外は G_min へ
        region = voice_region(S.inverse(G * S.X), sr, S.t, ps.mean(axis=0))
        G = gmin + (G - gmin) * region[None, :]
    speech = S.inverse(G * S.X)
    env = S.inverse((1 - G) * S.X)
    return Separation(speech, env, G, lam, p, S, region)
