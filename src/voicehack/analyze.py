"""解析パイプライン: 1 つの音声から数値レポート (JSON/CSV)・図・分離音声を出力."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from . import plots
from .audio_io import Audio, save
from .denoise import separate
from .loudness import level_summary, loudness, rms_db
from .pitch import pitch_summary, pitch_track
from .rate import modulation_rate, rate_summary, speech_rate, subband_rate
from .spectral import octave_bands, spectral_features, spectrum, stft


def analyze(audio: Audio, out_dir: str | Path, separate_noise: bool = True,
            asr: bool = False, dnn: bool = False,
            near_field_db: float | None = 15.0) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x, sr = audio.x, audio.sr

    # --- ノイズ分離: 韻律 (トーン/スピード) は分離後の音声で測る
    sep = separate(x, sr, near_field_db=near_field_db) if separate_noise else None
    if sep is not None and dnn:
        # 声と重なった突発音も分ける: SepFormer の音声推定 + 周期性ゲート
        from .dnn import enhance_checked, gate_by_voice_region

        y, repairs = enhance_checked(x, sr, sep.speech)
        sep.speech = gate_by_voice_region(x, y, sr, sep=sep)
        sep.environment = x - sep.speech
        sep.method = "SepFormer (DNS4, per utterance) + voice-region gate"
    voice = sep.speech if sep is not None else x

    # --- スペクトル / 数値化
    S = stft(x, sr)
    feats = spectral_features(S)
    sp = spectrum(x, sr)
    specs = {"original": sp}
    if sep is not None:
        specs["speech"] = spectrum(sep.speech, sr)
        specs["environment"] = spectrum(sep.environment, sr)

    # --- 大きさ / トーン / スピード
    loud = loudness(x, sr)
    rms = rms_db(x, sr)
    pitch = pitch_track(voice, sr)
    rate = speech_rate(voice, sr, pitch)
    wn = subband_rate(voice, sr, pitch)
    region = sep.voice_region if sep is not None else None
    mrate = modulation_rate(voice, sr, active=None if region is None else region > 0.5)

    active = rms[1] > (np.max(rms[1]) - 40)  # 無音フレームを除いて特徴量の統計を取る
    act_S = np.interp(S.t, rms[0], active.astype(float)) > 0.5
    summary = {
        "file": {"duration_s": audio.duration, "sample_rate": sr},
        "level": level_summary(x),
        "loudness": {k: loud[k] for k in
                     ("integrated_lufs", "loudness_range_lu", "max_momentary_lufs")},
        "spectrum": {
            "dominant_peaks": sp.peaks,
            "octave_bands": octave_bands(x, sr),
            **{f"{k}_median": float(np.median(v[act_S])) if act_S.any() else None
               for k, v in feats.items()},
        },
        "pitch": pitch_summary(pitch),
        "rate": rate_summary(rate, wn, mrate),
    }
    if asr:
        from .asr import mora_rate

        summary["rate"]["asr"] = mora_rate(voice, sr, asr_input=x)
    if sep is not None:
        summary["separation"] = {
            "method": "SepFormer (DNS4) + voice-region gate" if dnn else
                      "MCRA + MMSE-LSA + OM-LSA + voice-region gate",
            "speech": {**level_summary(sep.speech),
                       "integrated_lufs": loudness(sep.speech, sr)["integrated_lufs"],
                       "dominant_peaks": specs["speech"].peaks},
            "environment": {**level_summary(sep.environment),
                            "integrated_lufs": loudness(sep.environment, sr)["integrated_lufs"],
                            "dominant_peaks": specs["environment"].peaks,
                            "octave_bands": octave_bands(sep.environment, sr)},
        }
        if dnn:
            summary["separation"]["dnn_segments"] = repairs
        save(out / "speech.wav", Audio(sep.speech, sr))
        save(out / "environment.wav", Audio(sep.environment, sr))

    # --- 出力
    (out / "report.json").write_text(json.dumps(_clean(summary), ensure_ascii=False, indent=2))
    _frames_csv(out / "frames.csv", S, feats, rms, loud, pitch, sep)
    plots.spectrum_plot(specs, out / "spectrum.png")
    plots.spectrogram_plot(S, sr, out / "spectrogram.png", pitch=pitch)
    plots.prosody_plot(x, sr, loud, rms, pitch, rate, wn, summary, out / "prosody.png")
    if sep is not None:
        plots.separation_plot(sep, sr, out / "separation.png")
    return summary


def _frames_csv(path, S, feats, rms, loud, pitch, sep):
    t = S.t
    cols = {
        "time_s": t,
        "rms_dbfs": np.interp(t, rms[0], rms[1]),
        "momentary_lufs": np.interp(t, loud["momentary"][0] - 0.2, loud["momentary"][1]),
        "f0_hz": np.interp(t, pitch.t, pitch.f0, left=np.nan, right=np.nan),
        **feats,
    }
    if sep is not None:
        cols["speech_presence"] = sep.speech_prob.mean(axis=0)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols.keys())
        for row in zip(*cols.values()):
            w.writerow([f"{v:.4f}" if np.isfinite(v) else "" for v in row])


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return round(float(o), 3) if np.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    return o
