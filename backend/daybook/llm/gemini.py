"""The only module that talks to Gemini. Copied from Lore's `lore/llm.py` and extended (plan 6.1).

Changes from Lore:
- `generate()` takes `thinking_level` (DB-04) and reports `finish_reason` / `blocked`, so callers can
  answer a SAFETY block with a template line (plan 14.4).
- Tool-calling helpers are dropped: no Daybook agent has tools.
- Costs come from `obs/pricing.yaml`; embeddings return token-free estimates.
- `tts()` (Gemini TTS) is the voice fallback until ElevenLabs is set up (DB-03).

`GeminiLLM` calls the google-genai SDK with a timeout and retries with exponential backoff on rate
limits (429) and server errors (5xx). Fakes live in `llm/fakes.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from daybook.obs.cost import llm_cost
from daybook.obs.log import log_event
from daybook.settings import Settings

EmbedTask = Literal["query", "document"]
ThinkingLevel = Literal["minimal", "low", "medium", "high"]


@dataclass
class LLMResponse:
    """One model reply, reduced to what the agents need."""

    text: str = ""
    parsed: Any = None  # a Pydantic object when a response_schema was given and the JSON was valid
    tokens_in: int = 0
    tokens_out: int = 0  # includes thinking tokens (they're billed as output)
    model: str = ""
    finish_reason: str = "STOP"
    blocked: bool = False  # prompt blocked or the reply stopped for SAFETY
    latency_ms: int = 0

    @property
    def cost_usd(self) -> float:
        return llm_cost(self.model, self.tokens_in, self.tokens_out)


class LLM(Protocol):
    async def generate(
        self,
        model: str,
        system: str,
        contents: list[types.Content],
        response_schema: type[BaseModel] | None = None,
        thinking_level: ThinkingLevel | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...

    async def embed(self, texts: list[str], task: EmbedTask) -> list[list[float]]: ...


# ------------------------------------------------------------------ building history messages


def user_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


def model_text(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part.from_text(text=text)])


# ------------------------------------------------------------------ the real client


class GeminiLLM:
    """google-genai wrapper with a timeout and retry/backoff."""

    RETRY_CODES = {429, 500, 502, 503, 504}

    def __init__(self, settings: Settings, timeout_s: float = 30.0, attempts: int = 3):
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.timeout_s = timeout_s
        self.attempts = attempts

    async def generate(
        self,
        model,
        system,
        contents,
        response_schema=None,
        thinking_level=None,
        temperature=None,
    ):
        config = types.GenerateContentConfig(system_instruction=system)
        if thinking_level:
            config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level)
        if temperature is not None:
            config.temperature = temperature
        if response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = response_schema

        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await self._with_retry(
            lambda: self.client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        )
        usage = resp.usage_metadata
        finish = _finish_reason(resp)
        blocked = finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "BLOCKED") or bool(
            getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        )
        out = LLMResponse(
            text=_safe_text(resp),
            parsed=None if blocked else _parse(resp, response_schema),
            tokens_in=(usage.prompt_token_count or 0) if usage else 0,
            tokens_out=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
            if usage
            else 0,
            model=model,
            finish_reason=finish,
            blocked=blocked,
            latency_ms=int((loop.time() - start) * 1000),
        )
        log_event(
            event="llm_call",
            model=model,
            tokens_in=out.tokens_in,
            tokens_out=out.tokens_out,
            cost_usd=round(out.cost_usd, 6),
            finish=finish,
            latency_ms=out.latency_ms,
        )
        return out

    async def embed(self, texts: list[str], task: EmbedTask) -> list[list[float]]:
        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        config = types.EmbedContentConfig(
            output_dimensionality=self.settings.embed_dim, task_type=task_type
        )
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 100):  # the API takes at most 100 texts per call
            batch = texts[start : start + 100]
            # One Content per text: gemini-embedding-2 merges a plain list of strings into a
            # single embedding (Lore FIX_LOG F-02).
            contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in batch]
            resp = await self._with_retry(
                lambda c=contents: self.client.aio.models.embed_content(
                    model=self.settings.embed_model, contents=c, config=config
                )
            )
            if len(resp.embeddings) != len(batch):
                raise RuntimeError(f"asked for {len(batch)} embeddings, got {len(resp.embeddings)}")
            vectors.extend(list(e.values) for e in resp.embeddings)
        return vectors

    async def _with_retry(self, make_call: Callable[[], Any]) -> Any:
        """Run `make_call()` with a timeout; on 429/5xx/timeout wait 1s, 2s, 4s... and retry."""
        for attempt in range(self.attempts):
            try:
                return await asyncio.wait_for(make_call(), timeout=self.timeout_s)
            except (errors.APIError, TimeoutError) as exc:
                code = getattr(exc, "code", None)
                retryable = isinstance(exc, TimeoutError) or code in self.RETRY_CODES
                if not retryable or attempt == self.attempts - 1:
                    raise
                wait = 2**attempt
                log_event(event="llm_retry", attempt=attempt + 1, code=code, wait_s=wait)
                await asyncio.sleep(wait)


class GeminiTTS:
    """Gemini text-to-speech (single speaker). Returns WAV bytes (24 kHz mono 16-bit)."""

    def __init__(self, settings: Settings, timeout_s: float = 20.0):
        self.settings = settings
        self.client = genai.Client(api_key=settings.tts_key)
        self.timeout_s = timeout_s

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.settings.gemini_tts_voice
                    )
                )
            ),
        )
        resp = await asyncio.wait_for(
            self.client.aio.models.generate_content(
                model=self.settings.gemini_tts_model, contents=text, config=config
            ),
            timeout=self.timeout_s,
        )
        part = resp.candidates[0].content.parts[0].inline_data
        data, mime = part.data, (part.mime_type or "")
        if not data.startswith(b"RIFF"):  # raw PCM: wrap it
            from daybook.voice.wav import pcm_to_wav

            data = pcm_to_wav(data)
        return data, "audio/wav"


def _finish_reason(resp: Any) -> str:
    try:
        fr = resp.candidates[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return "BLOCKED" if getattr(resp, "prompt_feedback", None) else "UNKNOWN"
    return getattr(fr, "name", str(fr)) if fr is not None else "STOP"


def _safe_text(resp: Any) -> str:
    try:
        parts = resp.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return ""
    texts = [p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)]
    return "".join(texts)


def _parse(resp: Any, schema: type[BaseModel] | None) -> Any:
    """Return the structured object, or None if the model's JSON did not match the schema."""
    if schema is None:
        return None
    if isinstance(resp.parsed, schema):
        return resp.parsed
    try:
        return schema.model_validate_json(_safe_text(resp))
    except Exception:
        return None
