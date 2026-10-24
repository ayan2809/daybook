"""Tiny WAV helpers (pure Python)."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def silent_wav(seconds: float = 0.3, rate: int = 8000) -> bytes:
    return pcm_to_wav(b"\x00\x00" * int(seconds * rate), rate=rate)


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0
