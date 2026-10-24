"""Analyzer fast pass → DraftDelta; plus the edit-patch and pending-answer parsers (plan 6.3, 7.4)."""

from __future__ import annotations

import json
from datetime import date

from daybook.agents.base import Deps, history, structured
from daybook.agents.schemas import DraftDelta, EditPatch, PendingAnswer
from daybook.llm.gemini import user_text
from daybook.obs.tracer import Trace
from daybook.prompts import render


async def fast_pass(
    deps: Deps, trace: Trace, turns: list[dict], board: dict, today: date, mode: str
) -> DraftDelta | None:
    system, version = render(
        "analyzer_fast",
        today=today.isoformat(),
        mode=mode,
        board=json.dumps(board, ensure_ascii=False) if board else "(empty)",
    )
    contents = history([t for t in turns if not t.get("safety")], limit=8)
    delta, _ = await structured(
        deps,
        trace,
        "analyzer.fast",
        deps.settings.model_fast,
        system,
        version,
        contents,
        DraftDelta,
    )
    return delta


async def propose_edit(
    deps: Deps, trace: Trace, record: dict, request_text: str
) -> EditPatch | None:
    system, version = render("edit_patch", record=json.dumps(record, ensure_ascii=False))
    patch, _ = await structured(
        deps,
        trace,
        "analyzer.edit_patch",
        deps.settings.model_fast,
        system,
        version,
        [user_text(request_text)],
        EditPatch,
    )
    return patch


async def parse_pending_answer(
    deps: Deps, trace: Trace | None, question: str, item: dict | None, text: str
) -> PendingAnswer | None:
    system, version = render(
        "pending_answer", question=question, item=json.dumps(item or {}, ensure_ascii=False)
    )
    ans, _ = await structured(
        deps,
        trace,
        "analyzer.pending_answer",
        deps.settings.model_fast,
        system,
        version,
        [user_text(text)],
        PendingAnswer,
    )
    return ans
