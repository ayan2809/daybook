"""Cost estimates from obs/pricing.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache
def pricing() -> dict:
    return yaml.safe_load((Path(__file__).parent / "pricing.yaml").read_text())


def llm_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = pricing()
    rate_in, rate_out = p["llm_per_mtok"].get(model, p["default_llm"])
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


def embed_cost(model: str, tokens: int) -> float:
    return tokens * pricing()["embed_per_mtok"].get(model, 0.2) / 1_000_000


def tts_cost(model: str, chars: int, audio_seconds: float = 0.0) -> float:
    t = pricing()["tts"].get(model)
    if not t:
        return 0.0
    if "per_kchar" in t:
        return chars / 1000 * t["per_kchar"]
    text_tokens = chars / 4
    audio_tokens = audio_seconds * t["audio_tokens_per_sec"]
    return (text_tokens * t["in_per_mtok"] + audio_tokens * t["out_per_mtok"]) / 1_000_000
