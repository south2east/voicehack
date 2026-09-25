"""音声認識によるモーラ数ベースの発話速度 (要: uv sync --extra asr).

音量包絡の山を数える方式 (rate.py) は, 早口 (10 モーラ/s 超) で 5〜6 音節/s に
頭打ちになる (experiments/eval_rate.py). そこで文字起こしから読みを求め,
モーラ数 / 発話時間 で速さを出す. 日本語の話速研究で標準的な単位 (モーラ/秒).

- 音声認識: Whisper (A. Radford et al., "Robust speech recognition via large-scale
  weak supervision," ICML 2023) を faster-whisper (CTranslate2) で CPU 実行.
- 読み: 形態素解析 MeCab + UniDic (fugashi, unidic-lite) の発音形.
- モーラ: 拗音の小書き (ャュョァィゥェォヮ) は直前と合わせて 1, ッ・ン・ー は各 1.

Whisper は同じ文の繰り返しをまとめてしまうことがあるため, 0.5 s 以上の
ポーズで区切った区間ごとに認識する.

区間の検出は分離後の音声で行うが, 認識そのものは元の入力にかける.
音声強調の歪みは認識を悪化させうる (K. Iwamoto et al., "How bad are artifacts?:
Analyzing the impact of speech enhancement errors on ASR," Interspeech 2022).
実録音 (音楽を流しながら話す) でも, 入力では正しく認識された発話が, 従来法・DNN の
どちらの分離後でも「本屋で」→「方にやれ」と誤認識された.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .loudness import rms_db

_SMALL = set("ァィゥェォャュョヮ")
_DIGITS = "〇一二三四五六七八九"


@dataclass
class Utterance:
    start: float
    end: float
    text: str
    kana: str
    morae: int


@lru_cache(maxsize=2)
def _model(size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(size, device="cpu", compute_type="int8")


@lru_cache(maxsize=1)
def _tagger():
    import fugashi

    return fugashi.Tagger()


def _int_to_kanji(n: int) -> str:
    if n == 0:
        return "零"
    out = ""
    for unit, name in ((10 ** 8, "億"), (10 ** 4, "万")):
        if n >= unit:
            out += _int_to_kanji(n // unit) + name
            n %= unit
    for unit, name in ((1000, "千"), (100, "百"), (10, "十")):
        d, n = divmod(n, unit)
        if d:
            out += ("" if d == 1 else _DIGITS[d]) + name
    return out + (_DIGITS[n] if n else "")


def to_kana(text: str) -> str:
    """文字列 → カタカナ発音形 (数字は漢数字に直してから読ませる)."""
    text = re.sub(r"\d+", lambda m: _int_to_kanji(int(m.group())), text)
    out = []
    for w in _tagger()(text):
        pron = getattr(w.feature, "pron", None)
        out.append(pron if pron and pron != "*" else w.surface)
    return "".join(out)


def count_morae(kana: str) -> int:
    return sum(1 for c in kana if ("ァ" <= c <= "ヶ" or c == "ー") and c not in _SMALL)


def active_intervals(x: np.ndarray, sr: int, min_pause_s: float = 0.3,
                     min_len_s: float = 0.1) -> list[tuple[float, float]]:
    """発話区間: 30 ms RMS が 雑音床 (10 パーセンタイル) + 12 dB と
    上位レベル (99 パーセンタイル) - 35 dB の高い方を超える区間.
    後者は DNN 出力のように無音部がほぼ無音で雑音床が極端に低い場合の下限.
    min_pause_s 未満の隙間は埋める."""
    t, r = rms_db(x, sr)
    act = r > max(np.percentile(r, 10) + 12.0, np.percentile(r, 99) - 35.0)
    out: list[list[float]] = []
    start = None
    for i, a in enumerate(np.append(act, False)):
        if a and start is None:
            start = i
        elif not a and start is not None:
            a0, a1 = float(t[start]), float(t[i - 1])
            if out and a0 - out[-1][1] < min_pause_s:
                out[-1][1] = a1
            else:
                out.append([a0, a1])
            start = None
    return [(a, b) for a, b in out if b - a >= min_len_s]


def transcribe(x: np.ndarray, sr: int, model: str = "small",
               split_pause_s: float = 0.5, asr_input: np.ndarray | None = None) -> list[Utterance]:
    """x (分離後の音声) で発話区間を検出し, asr_input (元の入力; 既定は x) を認識する."""
    src = x if asr_input is None else asr_input
    if sr != 16000:
        from .audio_io import resample

        x, src, sr = resample(x, sr, 16000), resample(src, sr, 16000), 16000
    m = _model(model)
    utts = []
    for a, b in active_intervals(x, sr, min_pause_s=split_pause_s):
        seg = src[max(0, int((a - 0.2) * sr)): int((b + 0.2) * sr)].astype(np.float32)
        segs, _ = m.transcribe(seg, language="ja", beam_size=5,
                               condition_on_previous_text=False, vad_filter=False)
        text = "".join(s.text for s in segs).strip()
        if not text:
            continue
        kana = to_kana(text)
        utts.append(Utterance(a, b, text, kana, count_morae(kana)))
    return utts


def mora_rate(x: np.ndarray, sr: int, model: str = "small",
              asr_input: np.ndarray | None = None) -> dict:
    utts = transcribe(x, sr, model, asr_input=asr_input)
    morae = sum(u.morae for u in utts)
    if not utts:
        return {"morae": 0, "speech_rate_mora_per_s": None,
                "articulation_rate_mora_per_s": None, "utterances": []}
    span = utts[-1].end - utts[0].start
    # ポーズ (0.3 s 以上) を除いた発話時間
    phon = sum(b - a for a, b in active_intervals(x, sr)
               if b > utts[0].start and a < utts[-1].end)
    return {
        "morae": morae,
        "speech_rate_mora_per_s": morae / span if span > 0 else None,
        "articulation_rate_mora_per_s": morae / phon if phon > 0 else None,
        "speaking_span_s": span,
        "phonation_time_s": phon,
        "utterances": [{"start": u.start, "end": u.end, "text": u.text, "kana": u.kana,
                        "morae": u.morae} for u in utts],
    }
