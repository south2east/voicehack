"""Groq Cloud の Whisper API による文字起こし (`--asr groq`).

音声は Groq のサーバーへ送信される. API キーは環境変数 GROQ_API_KEY からのみ読み,
コードやリポジトリには置かない (README の「Groq API キーの設定」参照).
API は OpenAI 互換の POST /openai/v1/audio/transcriptions で, 追加パッケージは使わない.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3-turbo"


class GroqError(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise GroqError("環境変数 GROQ_API_KEY が設定されていません (README の「Groq API キーの設定」参照)")
    return key


def _multipart(fields: dict[str, str], file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: audio/flac\r\n\r\n".encode() + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _flac(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(x, -1, 1), sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def transcribe_segment(x: np.ndarray, sr: int, model: str = DEFAULT_MODEL, language: str = "ja",
                       retries: int = 4, timeout: float = 60.0) -> str:
    body, ctype = _multipart({"model": model, "language": language, "temperature": "0",
                              "response_format": "json"}, _flac(x, sr), "segment.flac")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key()}", "Content-Type": ctype,
        "User-Agent": "voicehack"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())["text"].strip()
        except urllib.error.HTTPError as e:
            # 429 (レート制限) と 5xx は待って再試行, それ以外は即エラー
            if e.code in (429, 500, 502, 503) and attempt < retries:
                wait = float(e.headers.get("retry-after") or 2 ** attempt)
                time.sleep(min(wait, 30.0))
                continue
            detail = e.read().decode(errors="replace")[:300]
            raise GroqError(f"Groq API エラー {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise GroqError(f"Groq API に接続できません: {e.reason}") from None
    raise GroqError("Groq API: 再試行回数を超えました")


def transcribe_segments(segs: list[np.ndarray], sr: int, model: str = DEFAULT_MODEL,
                        workers: int = 4) -> list[str]:
    """発話ごとの要求を並列に送る (Whisper が繰り返しをまとめる問題を避けるため発話単位)."""
    api_key()  # 送信前にキーの有無を確認
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda s: transcribe_segment(s, sr, model), segs))
