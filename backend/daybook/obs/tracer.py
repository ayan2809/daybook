"""Per-turn traces (plan 13.1): one JSON file per turn with spans named after the agents.

Attribute names follow the OpenTelemetry GenAI conventions (`gen_ai.*`) where one exists.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from daybook.llm.gemini import LLMResponse


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class Span:
    name: str
    kind: str  # root | llm | code | retrieval | tts | embed
    parent_id: str | None = None
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start: str = field(default_factory=_now_iso)
    latency_ms: int = 0
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id, "parent_id": self.parent_id, "name": self.name,
            "kind": self.kind, "start": self.start, "latency_ms": self.latency_ms,
            "status": self.status, "attrs": self.attrs,
        }


class Trace:
    def __init__(self, name: str = "turn", **meta: Any):
        self.meta = meta
        self.root = Span(name=name, kind="root")
        self.spans: list[Span] = [self.root]
        self._t0 = time.perf_counter()
        self.cost_usd = 0.0
        self.tokens_in = 0
        self.tokens_out = 0

    @contextmanager
    def span(self, name: str, kind: str = "code", **attrs: Any):
        s = Span(name=name, kind=kind, parent_id=self.root.span_id, attrs=dict(attrs))
        t = time.perf_counter()
        try:
            yield s
        except Exception as exc:
            s.status = "error"
            s.attrs["error"] = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            s.latency_ms = int((time.perf_counter() - t) * 1000)
            self.spans.append(s)

    def record_llm(self, s: Span, resp: LLMResponse, prompt_version: str, **extra: Any) -> None:
        s.kind = "llm"
        s.attrs.update({
            "gen_ai.system": "gcp.gemini",
            "gen_ai.request.model": resp.model,
            "gen_ai.usage.input_tokens": resp.tokens_in,
            "gen_ai.usage.output_tokens": resp.tokens_out,
            "gen_ai.response.finish_reasons": [resp.finish_reason],
            "daybook.prompt_version": prompt_version,
            "daybook.cost_usd": round(resp.cost_usd, 6),
            **extra,
        })
        self.add_cost(resp.cost_usd, resp.tokens_in, resp.tokens_out)

    def add_cost(self, cost: float, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.cost_usd += cost
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    def finish(self, **meta: Any) -> dict:
        self.root.latency_ms = int((time.perf_counter() - self._t0) * 1000)
        self.meta.update(meta)
        return {
            **self.meta,
            "totals": {
                "latency_ms": self.root.latency_ms,
                "cost_usd": round(self.cost_usd, 6),
                "input_tokens": self.tokens_in,
                "output_tokens": self.tokens_out,
            },
            "spans": [s.to_dict() for s in self.spans],
        }

    @staticmethod
    def dumps(d: dict) -> bytes:
        return json.dumps(d, default=str, ensure_ascii=False).encode()
