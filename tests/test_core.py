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


def test_voicing_gate_moves_isolated_knock_to_environment():
    # 0.5 s の調波音 (声) と, そこから 1 s 離れた机を叩くような減衰ノイズバースト
    rng = np.random.default_rng(4)
    n = SR * 4
    t = np.arange(n) / SR
    voice = 0.3 * sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12)) \
        * ((t > 1.0) & (t < 1.5))
    knock = np.zeros(n)
    k0 = int(2.8 * SR)
    knock[k0:k0 + 1600] = rng.standard_normal(1600) * np.exp(-np.arange(1600) / 200)
    y = voice + knock + 0.003 * rng.standard_normal(n)
    sep = separate(y, SR)
    w = slice(k0, k0 + 800)
    assert np.sum(sep.speech[w] ** 2) / np.sum(y[w] ** 2) < 0.05
    v = slice(int(1.1 * SR), int(1.4 * SR))
    assert np.sum(sep.speech[v] ** 2) / np.sum(y[v] ** 2) > 0.8


@pytest.mark.parametrize("rate", [3.0, 6.0])
def test_subband_rate_counts_syllable_train(rate):
    from voicehack.pitch import pitch_track
    from voicehack.rate import subband_rate
    # 母音 /a/ と /i/ 相当のフォルマントを交互に持つ音節列 (谷は -12 dB 程度まで)
    t = np.arange(int(4 * SR)) / SR
    ph = 2 * np.pi * np.cumsum(140 + 10 * np.sin(2 * np.pi * 0.5 * t)) / SR
    a = sum(np.sin(k * ph) * (1.5 if 5 <= k <= 6 else 0.4) / k for k in range(1, 30))
    i = sum(np.sin(k * ph) * (1.5 if k in (2, 16, 17) else 0.4) / k for k in range(1, 30))
    syl = np.floor(t * rate).astype(int) % 2
    am = 0.25 + 0.75 * np.sin(np.pi * (t * rate % 1.0)) ** 2
    x = 0.2 * np.where(syl == 0, a, i) * am * ((t > 0.5) & (t < 3.5))
    r = subband_rate(x, SR, pitch_track(x, SR))
    assert r["speech_rate_syll_per_s"] == pytest.approx(rate, rel=0.15)


def test_mora_counting():
    pytest.importorskip("fugashi")
    from voicehack.asr import count_morae, to_kana

    cases = {
        "駅前の本屋で雑誌を買いました": 18,   # っ・ん を 1 モーラと数える
        "来週の会議は3時に始まります": 19,     # 数字は漢数字に直して読む
        "毎朝コーヒーを飲みます": 13,          # 長音 ー を 1 モーラ
        "今日はいい天気です": 10,              # 拗音 きょ は 1 モーラ
    }
    for text, n in cases.items():
        assert count_morae(to_kana(text)) == n, (text, to_kana(text))


def test_pitch_track_follows_glide_beyond_speaker_range():
    from voicehack.pitch import pitch_track

    # 130 Hz 付近の平叙発話 3 s + 100→300 Hz へ滑らかに上がる「あー」2 s
    t1 = np.arange(3 * SR) / SR
    f1 = 130 + 10 * np.sin(2 * np.pi * 0.8 * t1)
    t2 = np.arange(2 * SR) / SR
    f2 = 100 * 3.0 ** (t2 / 2)
    f = np.concatenate([f1, f2])
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = 0.3 * sum(np.sin(k * ph) / k for k in range(1, 10))
    p = pitch_track(x, SR)
    late = (p.t > 4.75) & (p.t < 4.95)         # 真値 ≈ 262-290 Hz (声域上限 ≈ 1.5·Q3 より上)
    assert np.nanmin(p.f0[late]) > 255
    assert p.voiced[late].mean() > 0.9


def test_enhance_checked_per_utterance_and_fallback(monkeypatch):
    """発話ごとに DNN を掛け, 声を消した発話は従来法の出力に置き換える."""
    import voicehack.dnn as dnn

    t = np.arange(4 * SR) / SR
    ph = 2 * np.pi * np.cumsum(150 + 20 * np.sin(2 * np.pi * t)) / SR
    voice = 0.3 * sum(np.sin(k * ph) / k for k in range(1, 12))
    x = voice * (((t > 0.5) & (t < 1.3)) | ((t > 2.7) & (t < 3.5))) \
        + 0.002 * np.random.default_rng(5).standard_normal(len(t))
    conv = x.copy()
    calls = []

    def fake_enhance(sig, sr):
        calls.append(len(sig) / sr)
        # 2 つ目の発話 (呼び出し 2 回目) だけ声を消してしまう失敗
        return sig * (0.01 if len(calls) == 2 else 1.0)

    monkeypatch.setattr(dnn, "enhance", fake_enhance)
    y, log = dnn.enhance_checked(x, SR, conv)
    assert len(calls) == 2 and all(c < 2.5 for c in calls)   # 発話単位で呼ばれる
    assert [l["action"] for l in log] == ["dnn", "fallback"]
    seg1, seg2 = slice(int(0.7 * SR), int(1.1 * SR)), slice(int(2.9 * SR), int(3.3 * SR))
    assert np.allclose(y[seg1], x[seg1])
    assert np.allclose(y[seg2], conv[seg2])
    assert np.all(y[int(1.9 * SR):int(2.1 * SR)] == 0)          # 発話 ± 0.5 s の外は DNN に通さない


def test_cli_analyze_end_to_end(tmp_path):
    """合成音声 (声 + ハム + 机を叩くような突発音) を CLI で解析し, 出力一式と主要な値を確かめる."""
    import json

    import soundfile as sf

    from voicehack.cli import main

    rng = np.random.default_rng(6)
    t = np.arange(int(5 * SR)) / SR
    ph = 2 * np.pi * np.cumsum(160 + 30 * np.sin(2 * np.pi * 0.7 * t)) / SR
    syl = (np.sin(np.pi * 4 * t) ** 2) * ((t > 1.0) & (t < 3.5))
    voice = 0.3 * sum(np.sin(k * ph) / k for k in range(1, 15)) * syl
    knock = np.zeros_like(t)
    k0 = int(4.3 * SR)
    knock[k0:k0 + 1600] = 0.5 * rng.standard_normal(1600) * np.exp(-np.arange(1600) / 200)
    x = voice + 0.01 * np.sin(2 * np.pi * 60 * t) + 0.003 * rng.standard_normal(len(t)) + knock
    wav = tmp_path / "in.wav"
    sf.write(wav, x, SR, subtype="FLOAT")
    out = tmp_path / "out"
    main(["analyze", str(wav), "-o", str(out)])

    for f in ("report.json", "frames.csv", "spectrum.png", "spectrogram.png", "prosody.png",
              "separation.png", "speech.wav", "environment.wav"):
        assert (out / f).stat().st_size > 0, f
    r = json.loads((out / "report.json").read_text())
    assert 150 < r["pitch"]["median_f0_hz"] < 200
    assert r["rate"]["syllables"] >= 5
    sp, _ = sf.read(out / "speech.wav")
    en, _ = sf.read(out / "environment.wav")
    np.testing.assert_allclose(sp + en, x, atol=1e-5)
    w = slice(k0, k0 + 800)                       # 声から離れた突発音は環境音側へ
    assert np.sum(sp[w] ** 2) < 0.05 * np.sum(x[w] ** 2)


def test_near_field_filter_drops_far_quiet_voice():
    """最大の発話より 15 dB 以上小さい有声区間 (遠くの TV など) は環境音側に回す."""
    t = np.arange(4 * SR) / SR
    ph = 2 * np.pi * np.cumsum(150 + 20 * np.sin(2 * np.pi * t)) / SR
    voice = sum(np.sin(k * ph) / k for k in range(1, 12))
    main = 0.3 * voice * ((t > 0.5) & (t < 1.3))
    far = 0.015 * voice * ((t > 2.5) & (t < 3.3))      # -26 dB
    x = main + far + 0.0005 * np.random.default_rng(8).standard_normal(len(t))
    near = slice(int(0.7 * SR), int(1.1 * SR))
    fars = slice(int(2.7 * SR), int(3.1 * SR))
    on = separate(x, SR)
    off = separate(x, SR, near_field_db=None)
    assert np.sum(on.speech[near] ** 2) > 0.8 * np.sum(x[near] ** 2)
    assert np.sum(off.speech[fars] ** 2) > 0.5 * np.sum(x[fars] ** 2)   # 無効なら残る
    assert np.sum(on.speech[fars] ** 2) < 0.01 * np.sum(x[fars] ** 2)   # 有効なら落ちる
