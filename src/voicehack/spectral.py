"""スペクトル / スペクトログラム / スペクトル特徴量.

参考文献
- P. D. Welch, "The use of fast Fourier transform for the estimation of power
  spectra," IEEE Trans. Audio Electroacoust., 15(2), 1967.  -> 平均ピリオドグラム
- F. J. Harris, "On the use of windows for harmonic analysis with the discrete
  Fourier transform," Proc. IEEE, 66(1), 1978.  -> Hann 窓
- J. B. Allen, "Short term spectral analysis, synthesis, and modification by
  discrete Fourier transform," IEEE TASSP, 25(3), 1977.  -> STFT / OLA 再合成
- D. O'Shaughnessy, Speech Communication, 1987 (mel = 2595 log10(1 + f/700)).
- G. Peeters, "A large set of audio features for sound description," CUIDADO
  project report, IRCAM, 2004.  -> centroid / spread / rolloff / flatness
- J. D. Johnston, "Transform coding of audio signals using perceptual noise
  criteria," IEEE JSAC, 6(2), 1988.  -> spectral flatness measure
- IEC 61260-1:2014 / ANSI S1.11  -> オクターブバンド (中心周波数 1000·2^n)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import ShortTimeFFT, find_peaks, welch
from scipy.signal.windows import hann

EPS = 1e-12


def db(power: np.ndarray, ref: float = 1.0) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(power, EPS) / ref)


# --------------------------------------------------------------------------- #
# 長時間スペクトル (Welch)
# --------------------------------------------------------------------------- #
@dataclass
class Spectrum:
    f: np.ndarray       # Hz
    psd: np.ndarray     # power spectral density [1/Hz] (フルスケール正弦 = 0 dBFS 基準の片側)
    level_db: np.ndarray  # 各ビンのレベル [dBFS] (正弦波の振幅がそのまま読める power scaling)
    peaks: list[dict]


def spectrum(x: np.ndarray, sr: int, nfft: int | None = None, n_peaks: int = 8) -> Spectrum:
    nfft = nfft or _pow2(0.128 * sr)
    nperseg = min(nfft, len(x))
    f, psd = welch(x, sr, window="hann", nperseg=nperseg, noverlap=nperseg // 2,
                   scaling="density")
    _, pw = welch(x, sr, window="hann", nperseg=nperseg, noverlap=nperseg // 2,
                  scaling="spectrum")
    # full-scale sine (amp 1) の power は 0.5 -> 0 dBFS にするため ×2
    level = db(2.0 * pw)
    return Spectrum(f, psd, level, _peaks(f, level, n_peaks))


def _peaks(f: np.ndarray, level_db: np.ndarray, n: int) -> list[dict]:
    """突出度 (prominence) 上位 n 本のスペクトルピーク."""
    band = (f >= 20)
    idx, props = find_peaks(level_db * band + (-300) * (~band), prominence=6.0)
    order = np.argsort(props["prominences"])[::-1][:n]
    out = []
    for i in sorted(idx[order]):
        # 放物線補間で周波数分解能以下まで精密化 (Smith & Serra, 1987 の PARSHL と同様)
        fi, li = _parabolic(level_db, i)
        df = f[1] - f[0]
        out.append({"freq_hz": float(f[0] + fi * df), "level_dbfs": float(li)})
    return out


def _parabolic(y: np.ndarray, i: int) -> tuple[float, float]:
    if i <= 0 or i >= len(y) - 1:
        return float(i), float(y[i])
    a, b, c = y[i - 1], y[i], y[i + 1]
    den = a - 2 * b + c
    p = 0.5 * (a - c) / den if den != 0 else 0.0
    return i + p, b - 0.25 * (a - c) * p


def octave_bands(x: np.ndarray, sr: int) -> list[dict]:
    """1/1 オクターブバンドレベル [dBFS] (IEC 61260 の基準周波数系列)."""
    f, pw = welch(x, sr, window="hann", nperseg=min(_pow2(0.25 * sr), len(x)),
                  scaling="spectrum")
    pw = 2.0 * pw
    out = []
    for n in range(-4, 4):  # 62.5 Hz ... 8 kHz
        fc = 1000.0 * 2.0 ** n
        lo, hi = fc / np.sqrt(2), fc * np.sqrt(2)
        if hi > sr / 2:
            break
        m = (f >= lo) & (f < hi)
        out.append({"center_hz": round(fc, 1), "level_dbfs": float(db(pw[m].sum()))})
    return out


# --------------------------------------------------------------------------- #
# STFT / スペクトログラム
# --------------------------------------------------------------------------- #
@dataclass
class STFT:
    sft: ShortTimeFFT
    X: np.ndarray       # complex (freq, frame)
    f: np.ndarray
    t: np.ndarray
    n: int              # 元信号長

    @property
    def power(self) -> np.ndarray:
        return np.abs(self.X) ** 2

    def inverse(self, X: np.ndarray | None = None) -> np.ndarray:
        return self.sft.istft(self.X if X is None else X, k1=self.n)


def stft(x: np.ndarray, sr: int, win_s: float = 0.032, hop_ratio: float = 0.25) -> STFT:
    """Hann 窓 (periodic) + 75% overlap: COLA を満たし完全再構成可能 (Allen 1977)."""
    n_win = _pow2(win_s * sr)
    hop = int(n_win * hop_ratio)
    w = hann(n_win, sym=False)
    sft = ShortTimeFFT(w, hop, sr, mfft=n_win, fft_mode="onesided")
    X = sft.stft(x)
    return STFT(sft, X, sft.f, sft.t(len(x)), len(x))


def spectrogram_db(S: STFT) -> np.ndarray:
    """フルスケール正弦 ≈ 0 dBFS になるよう窓の和で正規化."""
    w = S.sft.win
    return db(S.power * (2.0 / np.sum(w)) ** 2)


def mel_filterbank(sr: int, n_fft: int, n_mels: int = 80, fmin: float = 0.0,
                   fmax: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    fmax = fmax or sr / 2
    hz2mel = lambda f: 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)
    mel2hz = lambda m: 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)
    edges = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    fft_f = np.linspace(0, sr / 2, n_fft // 2 + 1)
    fb = np.zeros((n_mels, len(fft_f)))
    for m in range(n_mels):
        l, c, r = edges[m], edges[m + 1], edges[m + 2]
        up = (fft_f - l) / (c - l)
        down = (r - fft_f) / (r - c)
        fb[m] = np.maximum(0.0, np.minimum(up, down))
    return fb, edges[1:-1]


def mel_spectrogram_db(S: STFT, sr: int, n_mels: int = 80) -> tuple[np.ndarray, np.ndarray]:
    fb, centers = mel_filterbank(sr, S.sft.mfft, n_mels)
    return db(fb @ S.power), centers


# --------------------------------------------------------------------------- #
# フレーム毎のスペクトル特徴 (Peeters 2004)
# --------------------------------------------------------------------------- #
def spectral_features(S: STFT, rolloff: float = 0.85) -> dict[str, np.ndarray]:
    P = S.power
    f = S.f[:, None]
    tot = P.sum(axis=0) + EPS
    centroid = (f * P).sum(axis=0) / tot
    spread = np.sqrt(((f - centroid) ** 2 * P).sum(axis=0) / tot)
    cum = np.cumsum(P, axis=0) / tot
    roll = S.f[np.argmax(cum >= rolloff, axis=0)]
    A = np.abs(S.X) + EPS
    flat = np.exp(np.mean(np.log(A ** 2), axis=0)) / np.mean(A ** 2, axis=0)
    return {"centroid_hz": centroid, "spread_hz": spread, "rolloff_hz": roll,
            "flatness": flat}


def _pow2(n: float) -> int:
    return int(2 ** np.ceil(np.log2(max(n, 2))))
