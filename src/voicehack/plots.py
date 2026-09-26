"""matplotlib による図の生成 (英語ラベル: 日本語フォント依存を避ける)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from .spectral import mel_spectrogram_db, spectrogram_db  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
SERIES = {"original": "#2a78d6", "speech": "#eb6834", "environment": "#1baf7a"}
# 単色 (blue) の連続スケール: 低エネルギー = 面の色, 高エネルギー = 濃紺
SEQ = LinearSegmentedColormap.from_list(
    "vh_blue", [SURFACE, "#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.6,
    "legend.frameon": False, "legend.fontsize": 8,
})


def _spec_img(ax, img, t, f, vmax, dyn=80, ylabel="Frequency [Hz]"):
    m = ax.pcolormesh(t, f, img, cmap=SEQ, vmin=vmax - dyn, vmax=vmax, shading="auto",
                      rasterized=True)
    ax.set_ylabel(ylabel)
    ax.grid(False)
    return m


def spectrum_plot(specs: dict, path, fmax=None):
    """specs: name -> Spectrum. 長時間平均スペクトル + ピーク注記."""
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for name, sp in specs.items():
        ax.semilogx(sp.f[1:], sp.level_db[1:], color=SERIES.get(name, INK2), label=name)
    main = next(iter(specs.values()))
    for pk in main.peaks[:6]:
        ax.plot(pk["freq_hz"], pk["level_dbfs"], "o", ms=5, color=SERIES["original"],
                mec=SURFACE, mew=1.5)
        ax.annotate(f"{pk['freq_hz']:.0f} Hz", (pk["freq_hz"], pk["level_dbfs"]),
                    xytext=(0, 7), textcoords="offset points", ha="center", fontsize=7,
                    color=INK2)
    ax.set_xlim(20, fmax or main.f[-1])
    top = max(np.max(sp.level_db) for sp in specs.values())
    low = min(np.percentile(sp.level_db[1:], 2) for sp in specs.values())
    ax.set_ylim(max(low - 5, top - 90), top + 8)
    ax.set_xlabel("Frequency [Hz]")
    ax.set_ylabel("Level [dBFS]")
    ax.set_title("Long-term spectrum (Welch, Hann)")
    if len(specs) > 1:
        ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def spectrogram_plot(S, sr, path, pitch=None):
    D = spectrogram_db(S)
    M, _ = mel_spectrogram_db(S, sr)
    vmax = float(np.percentile(D, 99.9))
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    m = _spec_img(axes[0], D, S.t, S.f, vmax)
    axes[0].set_title("Spectrogram (STFT, 32 ms Hann, 75% overlap)")
    if pitch is not None:
        axes[0].plot(pitch.t, pitch.f0, color=SERIES["speech"], lw=1.6, label="F0 (YIN)")
        axes[0].legend(loc="upper right")
        axes[0].set_ylim(0, min(sr / 2, 5000))
    fig.colorbar(m, ax=axes[0], label="dBFS", pad=0.01)
    Mm = M - M.max() + vmax
    m2 = _spec_img(axes[1], Mm, S.t, np.arange(M.shape[0]), vmax, ylabel="Mel band")
    axes[1].set_title("Mel spectrogram (80 bands)")
    axes[1].set_xlabel("Time [s]")
    fig.colorbar(m2, ax=axes[1], label="dB", pad=0.01)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def separation_plot(sep, sr, path):
    from .spectral import stft
    S_sp = stft(sep.speech, sr)
    S_en = stft(sep.environment, sr)
    D0 = spectrogram_db(sep.S)
    vmax = float(np.percentile(D0, 99.9))
    fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=True)
    for ax, (name, S) in zip(axes[:3], [("original", sep.S), ("speech", S_sp),
                                        ("environment", S_en)]):
        _spec_img(ax, spectrogram_db(S), S.t, S.f, vmax)
        ax.set_title(f"{name}")
        ax.set_ylim(0, min(sr / 2, 8000))
    t = sep.S.t
    # 実際の出力から: フレーム毎に 音声側エネルギー / (音声側 + 環境音側)  (方式によらない)
    es, ee = S_sp.power.sum(axis=0), S_en.power.sum(axis=0)
    n = min(len(es), len(ee), len(t))
    share = es[:n] / (es[:n] + ee[:n] + 1e-20)
    axes[3].plot(t[:n], share, color=SERIES["original"], label="speech share of energy (output)")
    if sep.voice_region is not None:
        axes[3].plot(t, sep.voice_region, color=SERIES["environment"], lw=1.2,
                     label="voice region (periodicity gate)")
    axes[3].plot(t, sep.speech_prob.mean(axis=0), color=SERIES["speech"], lw=1.0,
                 label="speech presence (MCRA p, band mean)")
    axes[3].set_ylim(0, 1.02)
    axes[3].set_xlabel("Time [s]")
    axes[3].legend(loc="center right")
    axes[3].set_title(f"Separation: {getattr(sep, 'method', 'MCRA + OM-LSA + voice-region gate')}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def prosody_plot(x, sr, loud, rms, pitch, rate, wn, summary, path):
    fig, axes = plt.subplots(4, 1, figsize=(11, 11), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.3, 1.3, 1.3]})
    t = np.arange(len(x)) / sr
    ax = axes[0]
    ax.plot(t, x, color=SERIES["original"], lw=0.5)
    ax.set_title("Waveform")
    ax.set_ylabel("Amplitude")

    ax = axes[1]
    ax.plot(rms[0], rms[1], color=SERIES["original"], lw=1.0, label="RMS level (30 ms) [dBFS]")
    mt, ml = loud["momentary"]
    ax.plot(mt - 0.2, ml, color=SERIES["speech"], label="momentary loudness (400 ms) [LUFS]")
    ax.axhline(summary["loudness"]["integrated_lufs"], color=INK2, lw=1, ls="--")
    ax.annotate(f"integrated {summary['loudness']['integrated_lufs']:.1f} LUFS",
                (t[-1], summary["loudness"]["integrated_lufs"]), ha="right", va="bottom",
                fontsize=8, color=INK2)
    ax.set_ylim(max(-80, np.nanmin(rms[1]) - 3), 3)
    ax.set_ylabel("dB")
    ax.set_title("Loudness")
    ax.legend(loc="upper left", ncol=2)

    ax = axes[2]
    ax.plot(pitch.t, pitch.f0, color=SERIES["speech"], label="F0 (YIN) [Hz]")
    ps = summary["pitch"]
    if ps.get("median_f0_hz"):
        ax.axhline(ps["median_f0_hz"], color=INK2, lw=1, ls="--")
        ax.annotate(f"median {ps['median_f0_hz']:.0f} Hz ({ps['note']}), "
                    f"range {ps['f0_range_semitones']:.1f} st",
                    (t[-1], ps["median_f0_hz"]), ha="right", va="bottom", fontsize=8,
                    color=INK2)
    ax.set_ylabel("F0 [Hz]")
    ax.set_title("Tone: pitch contour")
    ax.legend(loc="upper left")

    ax = axes[3]
    ax.plot(wn["t"], wn["envelope_db"], color=SERIES["original"], lw=1.0,
            label="syllabic envelope (subband correlation) [dB]")
    ax.axhline(wn["threshold_db"], color=INK2, lw=1, ls=":")
    for a, b in rate.pauses:
        ax.axvspan(a, b, color=GRID, alpha=0.6, lw=0)
    if len(wn["nuclei_t"]):
        yi = np.interp(wn["nuclei_t"], wn["t"], wn["envelope_db"])
        ax.plot(wn["nuclei_t"], yi, "o", ms=6, color=SERIES["speech"], mec=SURFACE, mew=1.5,
                label="syllable nuclei")
    rs = summary["rate"]
    mr = rs.get("modulation_rate_hz")
    ax.set_title(f"Speed: {rs['syllables']} syllables, "
                 f"speech rate {rs['speech_rate_syll_per_s']:.2f} syll/s, "
                 f"articulation rate {rs['articulation_rate_syll_per_s']:.2f} syll/s"
                 + (f", modulation {mr:.1f} Hz" if mr else "")
                 + (f"\nASR: {rs['asr']['morae']} morae, "
                    f"{rs['asr']['speech_rate_mora_per_s']:.2f} mora/s"
                    if rs.get("asr") and rs["asr"].get("speech_rate_mora_per_s") else ""))
    ax.set_ylim(wn["threshold_db"] - 15, np.max(wn["envelope_db"]) + 5)
    ax.set_ylabel("dB")
    ax.set_xlabel("Time [s]")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
