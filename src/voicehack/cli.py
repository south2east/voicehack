"""コマンドライン:  voicehack analyze <file> | voicehack record -d 5"""

from __future__ import annotations

import argparse
from pathlib import Path

from .analyze import analyze
from .audio_io import load, record, save


def _f(v, nd: int = 1) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _print(s: dict) -> None:
    lv, ld, p, r = s["level"], s["loudness"], s["pitch"], s["rate"]
    print(f"\n== {s['file']['duration_s']:.2f} s @ {s['file']['sample_rate']} Hz ==")
    print(f"[大きさ]   peak {lv['peak_dbfs']:.1f} dBFS / RMS {lv['rms_dbfs']:.1f} dBFS / "
          f"{_f(ld['integrated_lufs'])} LUFS (LRA {_f(ld['loudness_range_lu'])} LU)")
    if p.get("median_f0_hz"):
        print(f"[トーン]   F0 中央値 {p['median_f0_hz']:.0f} Hz ({p['note']}), "
              f"5–95% {p['f0_p5_hz']:.0f}–{p['f0_p95_hz']:.0f} Hz, "
              f"抑揚 {p['f0_range_semitones']:.1f} 半音")
    else:
        print("[トーン]   有声区間なし")
    print(f"[スピード] {r['syllables']} 音節, 発話速度 {r['speech_rate_syll_per_s']:.2f} 音節/s, "
          f"調音速度 {r['articulation_rate_syll_per_s']:.2f} 音節/s, ポーズ {r['n_pauses']} 回")
    dj = r["de_jong_2009"]
    print(f"           参考: de Jong 法 {dj['speech_rate_syll_per_s']:.2f} 音節/s, "
          f"変調周波数 {_f(r['modulation_rate_hz'], 2)} Hz")
    if "asr" in r:
        a = r["asr"]
        print(f"[モーラ]   {a['morae']} モーラ, 発話速度 {_f(a['speech_rate_mora_per_s'], 2)} モーラ/s, "
              f"調音速度 {_f(a['articulation_rate_mora_per_s'], 2)} モーラ/s")
        for u in a["utterances"]:
            print(f"           {u['start']:6.2f}-{u['end']:6.2f}s  {u['text']}  ({u['morae']})")
    peaks = ", ".join(f"{pk['freq_hz']:.0f} Hz ({pk['level_dbfs']:.0f} dB)"
                      for pk in s["spectrum"]["dominant_peaks"][:5])
    print(f"[周波数]   主要ピーク: {peaks}")
    print(f"           スペクトル重心 {_f(s['spectrum']['centroid_hz_median'], 0)} Hz, "
          f"rolloff85% {_f(s['spectrum']['rolloff_hz_median'], 0)} Hz, "
          f"平坦度 {_f(s['spectrum']['flatness_median'], 3)}")
    if "separation" in s:
        e = s["separation"]["environment"]
        print(f"[環境音]   RMS {e['rms_dbfs']:.1f} dBFS, {_f(e['integrated_lufs'])} LUFS")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="voicehack")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="音声ファイルを解析")
    a.add_argument("input")
    a.add_argument("-o", "--out", default=None, help="出力先 (既定: out/<ファイル名>)")
    a.add_argument("--sr", type=int, default=None, help="解析前にリサンプル")
    a.add_argument("--no-separate", action="store_true", help="ノイズ分離をしない")
    a.add_argument("--dnn", action="store_true",
                   help="深層学習 (SepFormer) で声と重なった物音も分離 (要: uv sync --extra dnn)")
    a.add_argument("--asr", action="store_true",
                   help="音声認識でモーラ速度を測る (要: uv sync --extra asr)")

    r = sub.add_parser("record", help="マイク録音して解析")
    r.add_argument("-d", "--duration", type=float, default=5.0)
    r.add_argument("--sr", type=int, default=16000)
    r.add_argument("-o", "--out", default="out/recording")
    r.add_argument("--asr", action="store_true", help="音声認識でモーラ速度を測る")
    r.add_argument("--dnn", action="store_true", help="深層学習で声と重なった物音も分離")

    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        audio = load(args.input, sr=args.sr)
        out = args.out or f"out/{Path(args.input).stem}"
        s = analyze(audio, out, separate_noise=not args.no_separate, asr=args.asr, dnn=args.dnn)
    else:
        audio = record(args.duration, args.sr)
        out = args.out
        Path(out).mkdir(parents=True, exist_ok=True)
        save(Path(out) / "input.wav", audio)
        s = analyze(audio, out, asr=args.asr, dnn=args.dnn)
    _print(s)
    print(f"\n出力: {out}/  (report.json, frames.csv, *.png, speech.wav, environment.wav)")


if __name__ == "__main__":
    main()
