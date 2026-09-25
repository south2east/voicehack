import numpy as np
import pytest

from voicehack.denoise import separate
from voicehack.loudness import k_weighting_coeffs, level_summary, loudness
from voicehack.pitch import yin
from voicehack.spectral import spectrum, stft

SR = 16000


def test_k_weighting_matches_bs1770_48k_table():
    # ITU-R BS.1770-4 Table 1 / Table 2 (48 kHz)
    (b1, a1), (b2, a2) = k_weighting_coeffs(48000)
    np.testing.assert_allclose(b1, [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-8)
    np.testing.assert_allclose(a1, [1.0, -1.69065929318241, 0.73248077421585], atol=1e-8)
    np.testing.assert_allclose(b2, [1.0, -2.0, 1.0])
    np.testing.assert_allclose(a2, [1.0, -1.99004745483398, 0.99007225036621], atol=1e-8)


@pytest.mark.parametrize("sr", [16000, 44100, 48000])
def test_997hz_full_scale_sine_is_minus_3_lufs(sr):
    # BS.1770-4: 0 dBFS, 997 Hz 正弦波を 1ch に入力 -> -3.01 LKFS
    t = np.arange(int(5 * sr)) / sr
    x = np.sin(2 * np.pi * 997 * t)
    assert loudness(x, sr)["integrated_lufs"] == pytest.approx(-3.01, abs=0.05)
    assert level_summary(x)["rms_dbfs"] == pytest.approx(0.0, abs=0.01)


def test_spectrum_peak_frequency_and_level():
    t = np.arange(SR * 2) / SR
    x = 0.5 * np.sin(2 * np.pi * 440.0 * t) + 0.1 * np.sin(2 * np.pi * 1234.0 * t)
    sp = spectrum(x, SR)
    f = sorted(sp.peaks, key=lambda p: -p["level_dbfs"])
    assert f[0]["freq_hz"] == pytest.approx(440.0, abs=2.0)
    assert f[0]["level_dbfs"] == pytest.approx(20 * np.log10(0.5), abs=1.5)
    assert f[1]["freq_hz"] == pytest.approx(1234.0, abs=2.0)


def test_stft_perfect_reconstruction():
    x = np.random.default_rng(0).standard_normal(SR)
    S = stft(x, SR)
    np.testing.assert_allclose(S.inverse(), x, atol=1e-10)


@pytest.mark.parametrize("f0", [90.0, 150.0, 220.0, 380.0])
def test_yin_harmonic_tone(f0):
    t = np.arange(SR) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8)) * 0.3
    p = yin(x, SR)
    est = np.nanmedian(p.f0)
    assert p.voiced.mean() > 0.9
    assert abs(12 * np.log2(est / f0)) < 0.1  # 0.1 半音以内


def test_yin_unvoiced_on_noise():
    x = 0.1 * np.random.default_rng(1).standard_normal(SR)
    assert yin(x, SR).voiced.mean() < 0.1


def _snr(ref, est):
    return 10 * np.log10(np.sum(ref ** 2) / np.sum((ref - est) ** 2))


def test_separation_improves_snr_and_is_additive():
    rng = np.random.default_rng(2)
    n = SR * 4
    t = np.arange(n) / SR
    # 「音声」: 0.25 s ごとにオン/オフする F0 変動付き調波音
    f0 = 180 + 30 * np.sin(2 * np.pi * 0.7 * t)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    env = ((t % 0.5) < 0.3) & (t > 0.8)
    s = 0.3 * sum(np.sin(k * ph) / k for k in range(1, 15)) * env
    noise = 0.05 * rng.standard_normal(n)
    y = s + noise
    sep = separate(y, SR)
    np.testing.assert_allclose(sep.speech + sep.environment, y, atol=1e-8)
    gain_db = _snr(s, sep.speech) - _snr(s, y)
    assert gain_db > 5.0, gain_db
    # 環境音側は雑音に近い
    assert _snr(noise, sep.environment) > _snr(noise, y)


def test_leading_digital_silence_does_not_stick_speech_presence():
    # マイク起動直後の無音 0.15 s + 定常雑音 3 s (音声なし) -> 音声存在確率は低いはず
    rng = np.random.default_rng(3)
    y = np.concatenate([np.zeros(int(0.15 * SR)), 0.05 * rng.standard_normal(3 * SR)])
    sep = separate(y, SR)
    t = sep.S.t
    assert sep.speech_prob.mean(axis=0)[(t > 0.3) & (t < 2.0)].mean() < 0.2
