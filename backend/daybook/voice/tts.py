"""Voice out (plan 9.2): ElevenLabs, or Gemini TTS as the fallback (DB-03), or a silent fake.

Audio is cached in blob storage by sha256(provider|model|voice|text).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

import httpx

from daybook.obs.cost import tts_cost
from daybook.settings import Settings
from daybook.voice.normalize import for_speech
from daybook.voice.wav import silent_wav, wav_seconds


@dataclass
class Audio:
    data: bytes
    mime: str
    cost_usd: float
    cached: bool = False


class TTS(Protocol):
    name: str
    model: str
    voice: str

    async def synthesize(self, text: str) -> tuple[bytes, str]: ...


class FakeTTS:
    name, model, voice = "fake", "fake", "silent"

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        return silent_wav(0.3), "audio/wav"


class ElevenLabsTTS:
    """POST /v1/text-to-speech/{voice_id} (docs checked 2026-10-24). Untested until the key arrives."""

    name = "elevenlabs"

    def __init__(self, settings: Settings):
        self.key = settings.elevenlabs_api_key
        self.model = settings.eleven_model
        self.voice = settings.eleven_voice_id
        self.client = httpx.AsyncClient(timeout=15)

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        r = await self.client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}",
            params={"output_format": "mp3_44100_64"},
            headers={"xi-api-key": self.key, "Content-Type": "application/json"},
            json={"text": text, "model_id": self.model},
        )
        r.raise_for_status()
        return r.content, "audio/mpeg"


class GeminiVoice:
    name = "gemini"

    def __init__(self, settings: Settings):
        from daybook.llm.gemini import GeminiTTS

        self.impl = GeminiTTS(settings)
        self.model = settings.gemini_tts_model
        self.voice = settings.gemini_tts_voice

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        return await self.impl.synthesize(text)


def make_tts(settings: Settings) -> TTS | None:
    if settings.daybook_tts == "off":
        return None
    if settings.daybook_tts == "elevenlabs":
        if not (settings.elevenlabs_api_key and settings.eleven_voice_id):
            raise RuntimeError("DAYBOOK_TTS=elevenlabs needs ELEVENLABS_API_KEY and ELEVEN_VOICE_ID")
        return ElevenLabsTTS(settings)
    if settings.daybook_tts == "gemini":
        if not settings.tts_key:
            raise RuntimeError("DAYBOOK_TTS=gemini needs GEMINI_TTS_API_KEY or GEMINI_API_KEY")
        return GeminiVoice(settings)
    return FakeTTS()


def cache_key(tts: TTS, text: str) -> str:
    h = hashlib.sha256(f"{tts.name}|{tts.model}|{tts.voice}|{text}".encode()).hexdigest()
    return f"tts-cache/{h}.{'mp3' if tts.name == 'elevenlabs' else 'wav'}"


async def speak(tts: TTS, store, text: str) -> Audio:
    spoken = for_speech(text)
    key = cache_key(tts, spoken)
    hit = store.get(key)
    if hit is not None:
        mime = "audio/mpeg" if key.endswith(".mp3") else "audio/wav"
        return Audio(hit.data, mime, 0.0, cached=True)
    data, mime = await tts.synthesize(spoken)
    store.put(key, data, content_type=mime)
    secs = wav_seconds(data) if mime == "audio/wav" else 0.0
    return Audio(data, mime, tts_cost(tts.model, len(spoken), secs))
