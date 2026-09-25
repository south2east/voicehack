"""デモ用音声の生成: macOS `say` の音声 + 合成環境音 (ピンクノイズ・換気扇ハム・電子音)."""

import subprocess
import sys
from pathlib import Path

import numpy as np

from voicehack.audio_io import load, save, Audio

HERE = Path(__file__).parent
SR = 16000
TEXT = ("こんにちは。今日は音声解析のテストをしています。"
        "声の大きさ、高さ、そして話す速さを測ってみましょう。")


def tts(name: str, rate_wpm: int, voice: str = "Kyoko") -> Audio:
    aiff = HERE / f"{name}.aiff"
    subprocess.run(["say", "-v", voice, "-r", str(rate_wpm), "-o", str(aiff), TEXT], check=True)
    wav = HERE / f"{name}_raw.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(aiff),
                    str(wav)], check=True)
    aiff.unlink()
    return load(wav)


def environment(n: int, sr: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # ピンクノイズ (1/f): 周波数領域で整形
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / sr)
    X[1:] /= np.sqrt(f[1:])
    X[0] = 0
    pink = np.fft.irfft(X, n)
    pink /= np.std(pink)
    t = np.arange(n) / sr
    hum = sum(np.sin(2 * np.pi * 120 * k * t) / k for k in (1, 2, 3))  # 換気扇/電源ハム
    beep = np.sin(2 * np.pi * 2000 * t) * ((t % 2.0) < 0.15)          # 2 kHz 電子音 (2 s 毎)
    return 0.02 * pink + 0.01 * hum + 0.015 * beep


if __name__ == "__main__":
    for name, wpm in [("slow", 130), ("normal", 190), ("fast", 280)]:
        a = tts(name, wpm)
        pad = np.zeros(int(0.8 * SR))
        clean = np.concatenate([pad, a.x * 0.5, pad])
        save(HERE / f"{name}_clean.wav", Audio(clean, SR))
        save(HERE / f"{name}_noisy.wav", Audio(clean + environment(len(clean), SR), SR))
        (HERE / f"{name}_raw.wav").unlink()
        print(f"{name}: {len(clean) / SR:.2f} s", file=sys.stderr)
