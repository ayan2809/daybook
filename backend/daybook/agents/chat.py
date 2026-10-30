"""Chat agent: the voice you hear. Plain text, no tools (plan 6.3, 20.1).

system_instruction = the stable persona first (so implicit prompt caching can reuse it), then the
turn's operator note in a labelled block. User text only ever goes in `contents`.
"""

from __future__ import annotations

from daybook.agents.base import Deps, history
from daybook.llm.gemini import LLMResponse, user_text
from daybook.obs.tracer import Trace
from daybook.prompts import load

OPENER_CUE = "(Daybook was just opened; the user tapped Start and is listening.)"


def system_for(note: str) -> tuple[str, str]:
    persona, version = load("chat")
    return (
        f"{persona}\n\n=== OPERATOR NOTE (from the planner, for this reply only) ===\n{note}",
        version,
    )


async def reply(
    deps: Deps, trace: Trace, turns: list[dict], note: str, attempt: int = 0
) -> tuple[str | None, LLMResponse | None]:
    system, version = system_for(note)
    contents = history(turns, limit=12) or [user_text(OPENER_CUE)]
    with trace.span("chat" if attempt == 0 else f"chat.retry{attempt}", "llm") as s:
        try:
            resp = await deps.llm.generate(
                deps.settings.model_chat, system, contents, thinking_level="minimal"
            )
        except Exception as exc:
            s.status = "error"
            s.attrs["error"] = f"{type(exc).__name__}: {exc}"[:300]
            return None, None
        trace.record_llm(s, resp, version)
        if resp.blocked:
            s.status = "blocked"
            return None, resp
        return resp.text.strip(), resp
