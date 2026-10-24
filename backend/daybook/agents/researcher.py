"""Researcher (intent classifier) → TurnLabel (plan 6.1, 20.2)."""

from __future__ import annotations

import json
from datetime import date

from daybook.agents.base import Deps, structured
from daybook.agents.schemas import TurnLabel
from daybook.llm.gemini import user_text
from daybook.obs.tracer import Trace
from daybook.prompts import render


async def label_turn(
    deps: Deps,
    trace: Trace,
    text: str,
    today: date,
    tz: str,
    projects: list[dict],
    pending: dict | None,
    last_agent: str | None,
    awaiting: str | None,
) -> TurnLabel:
    system, version = render(
        "researcher",
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tz=tz,
        projects=json.dumps(
            [{"id": p["id"], "name": p["name"], "aliases": p.get("aliases", [])} for p in projects]
        ) or "[]",
        pending=json.dumps(pending) if pending else "none",
        last_agent=json.dumps(last_agent) if last_agent else "nothing yet",
        awaiting=awaiting or "nothing",
    )
    label, _ = await structured(
        deps, trace, "researcher", deps.settings.model_router, system, version,
        [user_text(text)], TurnLabel,
    )
    if label is None:
        label = TurnLabel(confidence=0.0)
    for s in trace.spans[-1:]:
        s.attrs.update({"daybook.intent": label.primary_intent,
                        "daybook.confidence": label.confidence})
    return label
