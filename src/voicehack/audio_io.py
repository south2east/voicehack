"""音声の読み込み・録音・書き出し."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


@dataclass
class Audio:
    x: np.ndarray  # mono float64, [-1, 1]
    sr: int

    @property
    def duration(self) -> float:
        return len(self.x) / self.sr


def load(path: str | Path, sr: int | None = None) -> Audio:
    """ファイルを読み込み mono float64 にする. sr 指定時は polyphase でリサンプル."""
    x, fs = sf.read(str(path), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    if sr is not None and sr != fs:
        x = resample(x, fs, sr)
        fs = sr
    return Audio(x, int(fs))


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    frac = Fraction(sr_out, sr_in).limit_denominator(1000)
    return resample_poly(x, frac.numerator, frac.denominator)


def record(seconds: float, sr: int = 16000) -> Audio:
    """マイクから録音 (要: uv sync --extra mic)."""
    try:
        import sounddevice as sd
    except ImportError as e:  # pragma: no cover
        raise SystemExit("録音には sounddevice が必要です: uv sync --extra mic") from e
    print(f"録音中… {seconds:.1f} 秒")
    x = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="float64")
    sd.wait()
    print("録音終了")
    return Audio(x[:, 0], sr)


def save(path: str | Path, audio: Audio) -> None:
    peak = np.max(np.abs(audio.x)) if len(audio.x) else 0.0
    x = audio.x / peak * 0.99 if peak > 1.0 else audio.x
    sf.write(str(path), x, audio.sr, subtype="PCM_16")
