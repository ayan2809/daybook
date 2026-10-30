"""Shared plumbing for the LLM agents: one structured call, traced and costed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.genai import types
from pydantic import BaseModel

from daybook.llm.gemini import LLM, LLMResponse, model_text, user_text
from daybook.obs.tracer import Trace
from daybook.settings import Settings


@dataclass
class Deps:
    llm: LLM
    settings: Settings


def history(turns: list[dict], limit: int = 12) -> list[types.Content]:
    """Replay recent turns as plain user / model text (plan 6.1). Must end with a user turn."""
    out = []
    for t in turns[-limit:]:
        out.append(user_text(t["text"]) if t["role"] == "user" else model_text(t["text"]))
    while out and out[0].role != "user":
        out.pop(0)
    return out


async def structured[T: BaseModel](
    deps: Deps,
    trace: Trace | None,
    span_name: str,
    model: str,
    system: str,
    version: str,
    contents: list[types.Content],
    schema: type[T],
    thinking: str | None = "minimal",
    **attrs: Any,
) -> tuple[T | None, LLMResponse | None]:
    """Run one JSON-schema call. Returns (parsed or None, response or None on error)."""
    if trace is None:
        trace = Trace("adhoc")
    with trace.span(span_name, "llm", **attrs) as s:
        try:
            resp = await deps.llm.generate(
                model, system, contents, response_schema=schema, thinking_level=thinking
            )
        except Exception as exc:  # network / API error: the caller falls back
            s.status = "error"
            s.attrs["error"] = f"{type(exc).__name__}: {exc}"[:300]
            return None, None
        trace.record_llm(s, resp, version)
        if resp.blocked:
            s.status = "blocked"
        elif resp.parsed is None:
            s.status = "parse_error"
        return resp.parsed, resp
