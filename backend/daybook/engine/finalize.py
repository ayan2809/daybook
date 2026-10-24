"""Finalize (worker): the Analyzer deep pass → validated, versioned memory (plan 5.1, 6.3, 8).

Idempotent: the deep-pass result is saved to the session file before the vault write, so a retry
never calls the LLM twice; a finalized session is skipped.

Validation (code, plan 8.2): source quotes must be the user's words (else the item is a draft with a
validation note, never dropped); metric numbers must appear in the user's words; deltas are computed
by code; bullets must be 30–220 chars with only supported numbers; every promised question exists.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime

from daybook.agents.base import structured
from daybook.agents.schemas import FinalItem, FinalizeResult, FinalMetric, FinalPending
from daybook.engine import state as st
from daybook.engine.app import App, User
from daybook.engine.state import SessionState
from daybook.llm.gemini import user_text
from daybook.memory import repo, retrieval
from daybook.memory.validate import bullet_ok, number_supported, numbers_unsupported, quote_in
from daybook.obs.log import log_event
from daybook.obs.tracer import Trace
from daybook.prompts import render
from daybook.storage.blob import PreconditionFailed

GAP_QUESTIONS = {
    "metric": "Do you have a before-and-after number for {t}?",
    "outcome": "What changed because of {t}?",
    "ownership": "What was your own part in {t}?",
    "how": "How did you do {t}?",
    "why_it_mattered": "Why did {t} matter?",
}
PENDING_KIND = {
    "metric": "metric",
    "outcome": "outcome",
    "ownership": "ownership",
    "how": "clarify",
    "why_it_mattered": "outcome",
}


def _lf(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


async def finalize_session(app: App, user: User, sid: str, now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    loaded = st.load(app.store, user.prefix, sid)
    if loaded is None:
        return {"status": "missing"}
    state, etag = loaded
    if state.status in ("finalized", "active"):
        return {"status": state.status}
    vault = app.vault(user)
    trace = Trace("finalize", session_id=sid, job="finalize_session")

    if state.finalize_result is None:
        if app.spend.status(user.role, now) == "over" and user.role == "owner":
            state.flags["finalize_queued"] = True
            _save(app, user, state, etag)
            log_event(event="finalize_queued", session_id=sid)
            return {"status": "queued"}
        state.status = "finalizing"
        etag = _save(app, user, state, etag)
        result = await _deep_pass(app, user, state, trace)
        state.finalize_result = result.model_dump()
        etag = _save(app, user, state, etag)
    result = FinalizeResult.model_validate(state.finalize_result)

    plan = _validate(state, result)
    try:
        written = vault.write(lambda conn: _write(conn, state, plan))
    except Exception as exc:
        log_event(event="finalize_failed", session_id=sid, error=str(exc)[:200])
        state.status = "failed"
        _save(app, user, state, etag)
        raise
    with trace.span("index", "embed"):
        try:
            await retrieval.index(vault, app.embedder)
        except Exception as exc:  # the items are saved; embeddings retry next time
            log_event(event="index_failed", session_id=sid, error=str(exc)[:200])
    with vault.read() as conn:
        items = repo.list_items(conn, ids=written["item_ids"], include_hidden=True)
        pend = [repo.get_pending(conn, p) for p in written["pending_ids"]]
    state.summary = {
        "day_summary": result.day_summary,
        "item_ids": written["item_ids"],
        "pending_ids": written["pending_ids"],
        "items": items,
        "pending_created": [p for p in pend if p],
    }
    state.status = "finalized"
    app.spend.add(user.role, trace.cost_usd, now)
    state.budgets["cost_usd"] = round(state.budgets["cost_usd"] + trace.cost_usd, 6)
    stats = session_stats(state)
    vault.write(
        lambda conn: conn.execute(
            "UPDATE sessions SET status = 'finalized', stats = ? WHERE id = ?",
            (json.dumps(stats), state.id),
        )
    )
    _save(app, user, state, None, force=True)
    d = trace.finish()
    app.store.put(f"traces/{user.uid}/{state.local_date}/jobs/finalize-{sid}.json", Trace.dumps(d))
    app.dispatch(
        "session_judge",
        uid=user.uid,
        prefix=user.prefix,
        sandbox_id=user.sandbox_id,
        session_id=sid,
    )
    return {"status": "finalized", "items": len(written["item_ids"])}


def _save(app: App, user: User, state: SessionState, etag: str | None, force: bool = False) -> str:
    """Save the session file; the worker is the only writer after the session ended, but a late
    beacon or tab may race, so on conflict we merge by re-reading and keeping our fields."""
    for _ in range(3):
        try:
            if force or etag is None:
                cur = app.store.head(st.session_key(user.prefix, state.id))
                return st.save(app.store, user.prefix, state, cur)
            return st.save(app.store, user.prefix, state, etag)
        except PreconditionFailed:
            etag = None
    raise PreconditionFailed(state.id)


# ------------------------------------------------------------------ the deep pass


async def _deep_pass(app: App, user: User, state: SessionState, trace: Trace) -> FinalizeResult:
    vault = app.vault(user)
    board = list(state.board_items.values())
    with vault.read() as conn:
        projects = repo.list_projects(conn)
        open_pending = repo.open_pending(conn)
        touched = repo.list_items(conn, ids=state.touched_items, include_hidden=True)
        keep_names = bool((repo.get_setting(conn, "app_settings", {}) or {}).get("keep_names"))
    similar = await _similar(app, user, state, board)
    payload = {
        "today": state.local_date,
        "mode": state.mode,
        "transcript": [
            {"seq": t.seq, "role": t.role, "text": t.text} for t in state.turns if not t.safety
        ],
        "draft_board": board,
        "projects": [{"id": p["id"], "name": p["name"], "aliases": p["aliases"]} for p in projects],
        "similar_existing": similar,
        "open_pending": [
            {"id": p["id"], "item_id": p["item_id"], "question": p["question"]}
            for p in open_pending
        ],
        "touched_this_session": [
            {
                "id": i["id"],
                "title": i["title"],
                "what": i["what"],
                "result": i["result"],
                "bullet": i["bullet"],
                "kind": i["kind"],
                "metrics": [
                    f"{m['name']}: {m['display']} (said: {m['source_text']})" for m in i["metrics"]
                ],
                "resolution": next(
                    (r["resolution"] for r in state.resolved_pending if r["item_id"] == i["id"]),
                    "edited",
                ),
            }
            for i in touched
        ],
        "promised_questions": [
            {"ref": p["temp_id"], "about": p["about"], "ask_hint": p.get("ask_hint")}
            for p in state.promised_questions
        ],
        "note": "Use the draft_board temp_id (t1, t2...) as the item's ref when it comes from the board.",
    }
    system, version = render(
        "analyzer_deep",
        names_rule=", unless the user's keep-names setting is on (it is)" if keep_names else "",
    )
    result, _ = await structured(
        app.deps,
        trace,
        "analyzer.deep",
        app.settings.model_deep,
        system,
        version,
        [user_text(json.dumps(payload, ensure_ascii=False))],
        FinalizeResult,
        thinking="low",
    )
    if result is not None:
        state.prompt_versions["analyzer_deep"] = version
        return result
    log_event(event="deep_pass_fallback", session_id=state.id)
    return fallback_result(state)


async def _similar(app: App, user: User, state: SessionState, board: list[dict]) -> list[dict]:
    """Existing items that may be the same work (cosine ≥ 0.85, within 14 days) → for merge."""
    if not board:
        return []
    texts = [f"{b['title']}. {b['what']}" for b in board]
    try:
        vecs = await app.embedder.embed(texts, "query")
    except Exception:
        return []
    out, seen = [], set()
    today = date.fromisoformat(state.local_date)
    with app.vault(user).read() as conn:
        for text, v in zip(texts, vecs, strict=True):
            res = retrieval.search(conn, v, text, app.embedder.model_name, k=3)
            for h in res.hits:
                if h.cosine < 0.85 or h.ref_id in seen or not h.occurred_on:
                    continue
                if (today - date.fromisoformat(h.occurred_on)).days > 14:
                    continue
                it = repo.get_item(conn, h.ref_id)
                if it and it["status"] not in ("hidden", "merged"):
                    seen.add(h.ref_id)
                    out.append(
                        {
                            "id": it["id"],
                            "date": it["occurred_on"],
                            "kind": it["kind"],
                            "title": it["title"],
                            "what": it["what"],
                            "result": it["result"],
                            "project": it["project"]["name"] if it["project"] else None,
                        }
                    )
    return out


def fallback_result(state: SessionState) -> FinalizeResult:
    """No LLM: turn the validated draft board into items so nothing is lost."""
    items, pending = [], []
    for tid, b in state.board_items.items():
        metrics = [
            FinalMetric(
                name=b["title"][:80],
                unit=m.get("unit") or "count",
                baseline=m.get("baseline"),
                final=m.get("final"),
                delta_pct=m.get("delta_pct"),
                source_text=m["text"],
            )
            for m in b.get("metrics_raw", [])
        ]
        items.append(
            FinalItem(
                ref=tid,
                kind=b["kind"],
                title=b["title"],
                what=b["what"],
                how=b.get("how"),
                result=b.get("result"),
                ownership=b.get("ownership"),
                quantifiable=b.get("quantifiable", "maybe"),
                project=None,
                occurred_on=state.local_date,
                metrics=metrics,
                source_quote=b["source_quote"],
            )
        )
    for g in state.gaps:
        if g.get("priority", 2) <= 2 and g["temp_id"] in state.board_items:
            t = _lf(state.board_items[g["temp_id"]]["title"])
            pending.append(
                FinalPending(
                    item_ref=g["temp_id"],
                    kind=PENDING_KIND[g["gap"]],
                    question=GAP_QUESTIONS[g["gap"]].format(t=t),
                )
            )
    titles = [_lf(b["title"]) for b in state.board_items.values()][:4]
    summary = (
        ("You talked about " + ", ".join(titles) + ".") if titles else "A short reflection today."
    )
    return FinalizeResult(
        day_summary=summary,
        energy=state.energy,
        mood_word=state.mood_word,
        items=items,
        pending_questions=pending,
    )


# ------------------------------------------------------------------ validation (code)


def _validate(state: SessionState, result: FinalizeResult) -> dict:
    users = state.user_texts()
    today = state.local_date
    touched = set(state.touched_items)
    items = []
    for it in result.items:
        notes = []
        existing = it.existing_id if it.action in ("update", "merge_into") else None
        quote_ok = quote_in(it.source_quote, users)
        if not quote_ok and not existing:
            notes.append("The source quote wasn't found in your words.")
        metrics = []
        if it.kind == "soft_skill":
            it = it.model_copy(update={"metrics": []})  # plan B2: soft skills carry no metrics
        for m in it.metrics:
            ok = quote_in(m.source_text, users) and all(
                number_supported(getattr(m, f), users) for f in ("baseline", "final", "delta_pct")
            )
            if ok:
                metrics.append(m.model_dump())
            else:
                notes.append(f"Dropped a number that wasn't in your words: {m.source_text[:60]!r}.")
        bullet = it.bullet if it.kind in ("achievement", "soft_skill") else None
        # bullets of updated items may use numbers saved earlier: _write checks them against the vault
        if bullet and existing not in touched:
            pool = users + [m["source_text"] for m in metrics]
            if not bullet_ok(bullet) or numbers_unsupported(bullet, pool):
                notes.append("The bullet was removed because it didn't pass checks.")
                bullet = None
        try:
            occurred = date.fromisoformat(it.occurred_on or today)
            occurred_on = min(occurred.isoformat(), today)
        except ValueError:
            occurred_on = today
        items.append(
            {
                "ref": it.ref or f"i{len(items) + 1}",
                "action": it.action,
                "existing_id": existing,
                "fields": {
                    "kind": it.kind,
                    "title": it.title[:200],
                    "what": it.what,
                    "how": it.how,
                    "result": it.result,
                    "bullet": bullet,
                    "scope": it.scope,
                    "impact": max(1, min(5, it.impact)) if it.impact else None,
                    "quantifiable": it.quantifiable,
                    "ownership": it.ownership,
                    "occurred_on": occurred_on,
                    "source_quote": it.source_quote
                    if quote_ok
                    else (it.source_quote or (users[0][:200] if users else "")),
                    "source_turn_seqs": it.source_turn_seqs,
                },
                "project": it.project.model_dump() if it.project else None,
                "metrics": metrics,
                "skills": it.skills[:8],
                "notes": notes,
            }
        )
    return {
        "items": items,
        "pending": [p.model_dump() for p in result.pending_questions],
        "resolved": [r.model_dump() for r in result.resolved_pending],
        "links": [link.model_dump() for link in result.links],
        "day_summary": result.day_summary,
        "energy": result.energy,
        "mood_word": result.mood_word,
    }


def _write(conn, state: SessionState, plan: dict) -> dict:
    """The vault write. Re-runnable (it may run again after a 412)."""
    sid = state.id
    turns = [t for t in state.turns if not t.safety]
    repo.upsert_session(
        conn,
        {
            "id": sid,
            "mode": state.mode if state.mode != "quick" else "quick",
            "local_date": state.local_date,
            "started_at": state.started_at,
            "ended_at": state.ended_at,
            "status": "finalizing",
            "end_reason": state.end_reason,
            "turn_count": len([t for t in turns if t.role == "user"]),
            "energy": plan["energy"] or state.energy,
            "mood_word": plan["mood_word"] or state.mood_word,
            "prompt_versions": state.prompt_versions,
        },
    )
    repo.insert_turns(conn, sid, [t.model_dump() for t in turns])
    ref_to_id: dict[str, int] = {}
    item_ids: list[int] = []
    for it in plan["items"]:
        f = dict(it["fields"])
        p = it["project"]
        if (
            p
            and p.get("existing_id")
            and conn.execute("SELECT 1 FROM projects WHERE id = ?", (p["existing_id"],)).fetchone()
        ):
            f["project_id"] = p["existing_id"]
        elif p and p.get("new_name"):
            f["project_id"] = repo.project_id_for(conn, p["new_name"])
        existing = it["existing_id"]
        if (
            existing
            and conn.execute(
                "SELECT 1 FROM memory_items WHERE id = ? AND status != 'merged'", (existing,)
            ).fetchone()
        ):
            cur = repo.get_item(conn, existing)
            have = [metric_key(m) for m in cur["metrics"]]
            changes = {
                k: f[k]
                for k in (
                    "title",
                    "what",
                    "how",
                    "result",
                    "scope",
                    "impact",
                    "ownership",
                    "quantifiable",
                )
                if f.get(k)
            }
            if f.get("bullet"):
                pool = [
                    *state.user_texts(),
                    *(m["source_text"] for m in cur["metrics"]),
                    *(m["source_text"] for m in it["metrics"]),
                ]
                if not numbers_unsupported(f["bullet"], pool):
                    changes["bullet"] = f["bullet"]
            if (
                cur["status"] == "draft"
                and not cur["validation_note"]
                and not any(
                    q["kind"] in ("metric", "outcome", "ownership") for q in cur["open_pending"]
                )
            ):
                changes["status"] = "active"
            repo.update_item(
                conn,
                existing,
                changes,
                "analyzer",
                f"session {sid}",
                add_metrics=_new_metrics(it["metrics"], have),
            )
            ref_to_id[it["ref"]] = existing
            item_ids.append(existing)
            continue
        f["validation_note"] = " ".join(it["notes"]) or None
        f["status"] = "draft" if it["notes"] else "active"
        if f.get("bullet") and not bullet_ok(f["bullet"]):
            f["bullet"] = None
        iid = repo.create_item(
            conn, f, it["metrics"], it["skills"], session_id=sid, reason=f"session {sid}"
        )
        ref_to_id[it["ref"]] = iid
        item_ids.append(iid)
    # pending questions: the deep pass's, plus every promised one it missed
    pending_ids: list[int] = []
    covered = set()

    def resolve_ref(ref: str) -> int | None:
        if ref.startswith("id:"):
            try:
                i = int(ref[3:])
            except ValueError:
                return None
            return (
                i
                if conn.execute("SELECT 1 FROM memory_items WHERE id = ?", (i,)).fetchone()
                else None
            )
        return ref_to_id.get(ref)

    for p in plan["pending"]:
        iid = resolve_ref(p["item_ref"])
        if iid is None and p["item_ref"] not in ref_to_id:
            continue
        pending_ids.append(repo.create_pending(conn, iid, p["kind"], p["question"]))
        covered.add(p["item_ref"])
    for pq in state.promised_questions:
        ref = pq["temp_id"]
        if ref in covered or ref not in ref_to_id:
            continue
        q = (
            pq.get("ask_hint")
            if (pq.get("ask_hint") or "").endswith("?")
            else GAP_QUESTIONS[pq["gap"]].format(t=_lf(pq["title"]))
        )
        pending_ids.append(repo.create_pending(conn, ref_to_id[ref], PENDING_KIND[pq["gap"]], q))
    # items with an open metric/outcome question stay drafts until it's answered
    for pid in pending_ids:
        q = repo.get_pending(conn, pid)
        if q and q["item_id"] and q["kind"] in ("metric", "outcome", "ownership"):
            item = repo.get_item(conn, q["item_id"], full=False)
            if item["status"] == "active" and item["session_id"] == sid:
                conn.execute("UPDATE memory_items SET status = 'draft' WHERE id = ?", (item["id"],))
    # pending the deep pass says were resolved (brain dumps), unless code already did it
    done = {r["pending_id"] for r in state.resolved_pending}
    open_ids = {p["id"] for p in repo.open_pending(conn)}
    for r in plan["resolved"]:
        pid = r["pending_id"]
        if pid in done or pid not in open_ids:
            continue
        if r["resolution"] == "deferred":
            continue
        repo.resolve_pending(conn, pid, r["resolution"], r.get("answer_text") or None)
    repo.mark_asked(
        conn, [p for p in state.asked_pending if p not in state.flags.get("skipped_pending", [])]
    )
    for link in plan["links"]:
        fid = ref_to_id.get(link["from_ref"])
        if (
            fid
            and conn.execute(
                "SELECT 1 FROM memory_items WHERE id = ?", (link["to_item_id"],)
            ).fetchone()
        ):
            repo.add_link(conn, fid, link["to_item_id"], link["kind"])
    # the day digest (several sessions in a day are joined)
    prev = conn.execute(
        "SELECT text, item_ids FROM digests WHERE kind = 'day' AND period_start = ?",
        (state.local_date,),
    ).fetchone()
    text = plan["day_summary"]
    ids = item_ids
    if prev and sid not in json.dumps(state.flags.get("digested", [])):
        if text not in prev["text"]:
            text = f"{prev['text']} {text}"
        ids = list(dict.fromkeys(json.loads(prev["item_ids"]) + item_ids))
    repo.upsert_digest(conn, "day", state.local_date, state.local_date, text, ids)
    return {
        "item_ids": list(dict.fromkeys(item_ids)),
        "pending_ids": list(dict.fromkeys(pending_ids)),
    }


def metric_key(m: dict) -> tuple:
    """Two metrics are the same fact when their stated numbers match (wording may differ)."""

    def r(v):
        return None if v is None else round(float(v), 4)

    return (
        r(m.get("baseline")),
        r(m.get("final")),
        r(m.get("delta_pct")) if m.get("baseline") is None and m.get("final") is None else None,
    )


def _new_metrics(candidates: list[dict], have: list[tuple]) -> list[dict]:
    out, seen = [], list(have)
    for m in candidates:
        k = metric_key(m)
        if k in seen:
            continue
        seen.append(k)
        out.append(m)
    return out


def session_stats(state: SessionState) -> dict:
    lat = sorted(state.turn_latencies)
    user_turns = [t for t in state.turns if t.role == "user" and not t.safety]
    chars = sum(len(t.text) for t in user_turns)
    dictated = sum(t.dictated_chars or 0 for t in user_turns)
    duration = 0
    if state.ended_at:
        duration = int(
            (
                datetime.fromisoformat(state.ended_at) - datetime.fromisoformat(state.started_at)
            ).total_seconds()
        )
    return {
        "cost_usd": round(state.budgets.get("cost_usd", 0), 6),
        "tokens_in": state.budgets.get("tokens_in", 0),
        "tokens_out": state.budgets.get("tokens_out", 0),
        "tts_chars": state.budgets.get("tts_chars", 0),
        "latency_p50_ms": int(statistics.median(lat)) if lat else None,
        "latency_p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
        "guard_fails": state.guard_fails,
        "duration_sec": max(0, duration),
        "spoken_pct": round(100 * dictated / chars) if chars else None,
        "turns": len(user_turns),
        "pending_asked": len(state.asked_pending),
        "pending_resolved": len(state.resolved_pending),
        "safety_ran": bool(state.flags.get("safety_ran")),
    }
