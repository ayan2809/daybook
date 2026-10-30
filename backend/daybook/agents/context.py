"""Context agent, the librarian (plan 6.1, 7.4): hybrid search, then a Flash-Lite verifier picks
found / ambiguous / none. The user confirms before anything changes.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from daybook.agents.base import Deps, structured
from daybook.agents.schemas import ContextCandidate, ContextResult, ContextVerdict, TimeRef
from daybook.llm.gemini import user_text
from daybook.memory import repo, retrieval
from daybook.memory.embed import Embedder
from daybook.memory.vault import Vault
from daybook.obs.tracer import Trace
from daybook.prompts import render


def spoken_date(d: str) -> str:
    """'2026-10-20' → 'Tuesday the 20th'."""
    dt = date.fromisoformat(d)
    n = dt.day
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{dt.strftime('%A')} the {n}{suf}"


def record_fact(item: dict) -> str:
    """The exact facts the Chat agent may read back."""
    s = f"On {spoken_date(item['occurred_on'])} you said: {item['title']}"
    if item.get("result"):
        s += f". {item['result']}"
    elif item.get("metrics"):
        m = item["metrics"][0]
        s += f". {m['source_text']}"
    return s.rstrip(".") + "."


def window_for(time_refs: list[TimeRef], pad_days: int = 3) -> tuple[str, str] | None:
    starts = [r.start_date for r in time_refs if r.start_date]
    ends = [r.end_date or r.start_date for r in time_refs if r.start_date]
    if not starts:
        return None
    try:
        lo = date.fromisoformat(min(starts)) - timedelta(days=pad_days)
        hi = date.fromisoformat(max(ends)) + timedelta(days=pad_days)
    except ValueError:
        return None
    return lo.isoformat(), hi.isoformat()


async def find(
    deps: Deps,
    vault: Vault,
    embedder: Embedder,
    hint: str,
    time_refs: list[TimeRef],
    trace: Trace,
    abstain_cosine: float,
    verify: bool = True,
) -> ContextResult:
    window = window_for(time_refs)
    with trace.span("context.embed", "embed"):
        qvec = await retrieval.embed_query(embedder, hint)
    with trace.span("context.search", "retrieval") as s:
        with vault.read() as conn:
            res = retrieval.search(conn, qvec, hint, embedder.model_name, date_window=window, k=5)
            items = {h.ref_id: repo.get_item(conn, h.ref_id) for h in res.hits[:3]}
        s.attrs.update(
            {
                "daybook.retrieval.ids": [h.ref_id for h in res.hits[:3]],
                "daybook.retrieval.scores": [round(h.score, 4) for h in res.hits[:3]],
                "daybook.retrieval.best_cosine": round(res.best_cosine, 3),
                "daybook.retrieval.keyword_hits": res.keyword_hits,
                "daybook.retrieval.window": window,
            }
        )
    if not res.hits or (res.best_cosine < abstain_cosine and res.keyword_hits == 0):
        return ContextResult(status="none", why="nothing above the threshold")
    cands = [
        ContextCandidate(
            item_id=h.ref_id,
            date=items[h.ref_id]["occurred_on"],
            title=items[h.ref_id]["title"],
            field_values={
                "what": items[h.ref_id]["what"],
                "result": items[h.ref_id]["result"],
                "metrics": [
                    m["display"] + " — " + m["source_text"] for m in items[h.ref_id]["metrics"]
                ],
                "fact": record_fact(items[h.ref_id]),
            },
            score=round(h.score, 4),
        )
        for h in res.hits[:3]
        if items.get(h.ref_id)
    ]
    if not verify:
        return _heuristic(cands)
    system, version = render(
        "context_verify",
        hint=hint,
        dates=json.dumps([r.model_dump() for r in time_refs]),
        candidates=json.dumps([c.model_dump() for c in cands], ensure_ascii=False),
    )
    verdict, _ = await structured(
        deps,
        trace,
        "context.verify",
        deps.settings.model_context,
        system,
        version,
        [user_text(hint)],
        ContextVerdict,
    )
    if verdict is None:
        return _heuristic(cands)
    ids = {c.item_id for c in cands}
    if verdict.status == "found" and verdict.chosen_item_id not in ids:
        return _heuristic(cands)
    keep = (
        cands
        if verdict.status != "found"
        else [c for c in cands if c.item_id == verdict.chosen_item_id]
    )
    return ContextResult(
        status=verdict.status,
        candidates=keep,
        chosen_item_id=verdict.chosen_item_id if verdict.status == "found" else None,
        why=verdict.why,
    )


def _heuristic(cands: list[ContextCandidate]) -> ContextResult:
    if not cands:
        return ContextResult(status="none")
    if len(cands) == 1 or cands[0].score >= cands[1].score * 1.15:
        return ContextResult(
            status="found",
            candidates=cands[:1],
            chosen_item_id=cands[0].item_id,
            why="clear top score",
        )
    return ContextResult(status="ambiguous", candidates=cands[:2], why="close scores")
