"""Timeline (memories), pending questions, Ask and Remind me (plan 7.2, 7.5, 11)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from daybook.agents import analyzer_fast
from daybook.agents import ask as ask_agent
from daybook.api.deps import ApiError, app_dep, current_user, now_dep, rate_limit
from daybook.api.sessions import _pending
from daybook.engine.app import App, User
from daybook.memory import repo, retrieval
from daybook.memory.validate import number_supported, quote_in
from daybook.obs.log import log_event
from daybook.obs.tracer import Trace

router = APIRouter(dependencies=[Depends(rate_limit)])


async def _reindex(app: App, user: User, item_ids: list[int]) -> None:
    try:
        await retrieval.index(app.vault(user), app.embedder, item_ids)
    except Exception as exc:  # the edit is saved; the doc refreshes at the next finalize
        log_event(event="reindex_failed", error=type(exc).__name__)


def _item_or_404(conn, item_id: int) -> dict:
    item = repo.get_item(conn, item_id)
    if not item:
        raise ApiError(404, "not_found", "No such memory.")
    return item


# ------------------------------------------------------------------ memories


@router.get("/memories")
def list_memories(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    project: int | None = None,
    kind: str | None = None,
    q: str | None = None,
    include_hidden: bool = False,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
):
    with app.vault(user).read() as conn:
        items = repo.list_items(conn, from_, to, project, kind, q, include_hidden)
        projects = [{"id": p["id"], "name": p["name"]} for p in repo.list_projects(conn)]
    return {"items": items, "projects": projects}


@router.get("/memories/{item_id}")
def get_memory(item_id: int, app: App = Depends(app_dep), user: User = Depends(current_user)):
    with app.vault(user).read() as conn:
        item = _item_or_404(conn, item_id)
        versions = repo.item_versions(conn, item_id)
    return {"item": item, "versions": versions}


class PatchBody(BaseModel):
    fields: dict
    reason: str | None = None


@router.patch("/memories/{item_id}")
async def patch_memory(
    item_id: int, body: PatchBody, app: App = Depends(app_dep), user: User = Depends(current_user)
):
    allowed = {"title", "what", "how", "result", "bullet", "kind", "ownership", "occurred_on"}
    fields = {
        k: (v.strip() if isinstance(v, str) else v) for k, v in body.fields.items() if k in allowed
    }
    if not fields:
        raise ApiError(422, "validation", "Nothing to change.")
    if "bullet" in fields and fields["bullet"] and not 30 <= len(fields["bullet"]) <= 220:
        raise ApiError(422, "validation", "A bullet must be 30–220 characters.")
    if "occurred_on" in fields:
        try:
            date.fromisoformat(fields["occurred_on"])
        except (TypeError, ValueError):
            raise ApiError(422, "validation", "occurred_on must be YYYY-MM-DD.") from None

    def write(conn):
        _item_or_404(conn, item_id)
        repo.update_item(conn, item_id, fields, "user_ui", body.reason or "edited in the app")

    try:
        app.vault(user).write(write)
    except ValueError as exc:
        raise ApiError(422, "validation", str(exc)) from None
    await _reindex(app, user, [item_id])
    with app.vault(user).read() as conn:
        return {"item": repo.get_item(conn, item_id)}


async def _set_status(app: App, user: User, item_id: int, status: str, reason: str) -> dict:
    def write(conn):
        _item_or_404(conn, item_id)
        repo.update_item(conn, item_id, {"status": status}, "user_ui", reason)

    app.vault(user).write(write)
    await _reindex(app, user, [item_id])
    with app.vault(user).read() as conn:
        return {"item": repo.get_item(conn, item_id)}


@router.post("/memories/{item_id}/hide")
async def hide(item_id: int, app: App = Depends(app_dep), user: User = Depends(current_user)):
    return await _set_status(app, user, item_id, "hidden", "hidden in the app")


@router.post("/memories/{item_id}/unhide")
async def unhide(item_id: int, app: App = Depends(app_dep), user: User = Depends(current_user)):
    with app.vault(user).read() as conn:
        item = _item_or_404(conn, item_id)
    status = "draft" if item["open_pending"] or item["validation_note"] else "active"
    return await _set_status(app, user, item_id, status, "unhidden in the app")


class RestoreBody(BaseModel):
    version: int


@router.post("/memories/{item_id}/restore")
async def restore(
    item_id: int, body: RestoreBody, app: App = Depends(app_dep), user: User = Depends(current_user)
):
    def write(conn):
        _item_or_404(conn, item_id)
        repo.restore_version(conn, item_id, body.version)

    try:
        app.vault(user).write(write)
    except KeyError:
        raise ApiError(404, "not_found", "No such version.") from None
    await _reindex(app, user, [item_id])
    with app.vault(user).read() as conn:
        return {"item": repo.get_item(conn, item_id)}


# ------------------------------------------------------------------ pending questions


def _local_today(app: App, user: User, now: datetime) -> date:
    with app.vault(user).read() as conn:
        tz = (repo.get_profile(conn) or {}).get("timezone") or app.settings.tz_default
    return now.astimezone(ZoneInfo(tz)).date()


@router.get("/pending")
def list_pending(
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    today = _local_today(app, user, now)
    with app.vault(user).read() as conn:
        return {"pending": [_pending(p) for p in repo.open_pending(conn, today.isoformat())]}


def _open_or_404(conn, pid: int) -> dict:
    p = repo.get_pending(conn, pid)
    if not p:
        raise ApiError(404, "not_found", "No such question.")
    if p["status"] != "open":
        raise ApiError(409, "session_ended", "That question is already closed.")
    return p


class AnswerBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/pending/{pid}/answer")
async def answer(
    pid: int,
    body: AnswerBody,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    with app.vault(user).read() as conn:
        p = _open_or_404(conn, pid)
        item = repo.get_item(conn, p["item_id"]) if p["item_id"] else None
    trace = Trace("pending_answer")
    ans = await analyzer_fast.parse_pending_answer(
        app.deps,
        trace,
        p["question"],
        {"title": item["title"], "what": item["what"]} if item else None,
        body.text,
    )
    app.spend.add(user, trace.cost_usd, now)
    if ans is None:
        return {
            "candidate": {
                "kind": "text",
                "readback": "Save this as your answer?",
                "metric": None,
                "text": body.text,
            }
        }
    metric = None
    if ans.kind == "metric" and ans.metric:
        m = ans.metric
        if quote_in(m.text, [body.text]) and all(
            number_supported(getattr(m, f), [body.text]) for f in ("baseline", "final", "delta_pct")
        ):
            metric = {
                "name": ans.metric_name or (item["title"] if item else "result"),
                "category": ans.category or "other",
                "unit": m.unit or "count",
                "baseline": m.baseline,
                "final": m.final,
                "delta_pct": m.delta_pct,
                "better_when": ans.better_when or "higher",
                "source_text": m.text,
                "approx": bool(
                    re.search(r"\b(about|around|roughly|under|over|nearly)\b", m.text, re.I)
                ),
            }
    kind = ans.kind if (ans.kind != "metric" or metric) else "text"
    return {
        "candidate": {
            "kind": kind,
            "readback": ans.readback if kind == ans.kind else "Save this as your answer?",
            "metric": metric,
            "text": body.text,
        }
    }


class ConfirmBody(BaseModel):
    candidate: dict


@router.post("/pending/{pid}/confirm")
async def confirm(
    pid: int,
    body: ConfirmBody,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    c = body.candidate
    kind, text = c.get("kind"), c.get("text") or ""
    tomorrow = (_local_today(app, user, now) + timedelta(days=1)).isoformat()

    def write(conn):
        p = _open_or_404(conn, pid)
        if kind == "metric":
            m = c.get("metric") or {}
            if (
                not m
                or not quote_in(m.get("source_text", ""), [text])
                or not all(
                    number_supported(m.get(f), [text]) for f in ("baseline", "final", "delta_pct")
                )
            ):
                raise ApiError(422, "validation", "That number isn't in your answer.")
            if p["item_id"]:
                repo.add_metric(conn, p["item_id"], m)
            repo.resolve_pending(conn, pid, "answered", text)
        elif kind == "defer":
            repo.snooze_pending(conn, pid, tomorrow, text)
        elif kind == "not_quantifiable":
            repo.resolve_pending(conn, pid, "not_quantifiable", text)
        else:
            repo.resolve_pending(conn, pid, "answered", text)
        if p["item_id"] and kind in ("metric", "text", "not_quantifiable"):
            item = repo.get_item(conn, p["item_id"])
            fields = (
                {"status": "active"}
                if item["status"] == "draft"
                and not item["open_pending"]
                and not item["validation_note"]
                else {}
            )
            repo.update_item(
                conn, p["item_id"], fields, "pending_answer", f"answered: {p['question']}"
            )
        return p

    p = app.vault(user).write(write)
    if p["item_id"]:
        await _reindex(app, user, [p["item_id"]])
    with app.vault(user).read() as conn:
        return {
            "pending": _pending(repo.get_pending(conn, pid)),
            "item": repo.get_item(conn, p["item_id"]) if p["item_id"] else None,
        }


@router.post("/pending/{pid}/snooze")
def snooze(
    pid: int,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    tomorrow = (_local_today(app, user, now) + timedelta(days=1)).isoformat()

    def write(conn):
        _open_or_404(conn, pid)
        repo.snooze_pending(conn, pid, tomorrow)
        repo.mark_asked(conn, [pid])

    app.vault(user).write(write)
    with app.vault(user).read() as conn:
        return {"pending": _pending(repo.get_pending(conn, pid))}


def _close(status: str):
    def handler(pid: int, app: App = Depends(app_dep), user: User = Depends(current_user)):
        def write(conn):
            _open_or_404(conn, pid)
            repo.resolve_pending(conn, pid, status)

        app.vault(user).write(write)
        with app.vault(user).read() as conn:
            return {"pending": _pending(repo.get_pending(conn, pid))}

    return handler


router.post("/pending/{pid}/not-quantifiable")(_close("not_quantifiable"))
router.post("/pending/{pid}/dismiss")(_close("dismissed"))


# ------------------------------------------------------------------ Ask and Remind me


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@router.post("/ask")
async def ask(
    body: AskBody,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    status = app.spend.status(user, now)
    if status == "over":
        raise ApiError(429, "budget", "Daily budget reached, try again tomorrow.")
    if user.role == "demo" and (
        status != "ok" or not app.demo_caps.take(user.sandbox_id, "asks", now)
    ):
        raise ApiError(429, "demo_cap", "The demo's question limit for today was reached.")
    trace = Trace("ask", question=body.question)
    today = _local_today(app, user, now)
    out = await ask_agent.ask(
        app.deps,
        app.vault(user),
        app.embedder,
        body.question,
        today,
        trace,
        app.settings.abstain_cosine,
    )
    app.spend.add(user, trace.cost_usd, now)
    d = trace.finish(abstained=out["abstained"])
    app.store.put(
        f"traces/{user.uid}/{today.isoformat()}/jobs/ask-{now.strftime('%H%M%S%f')}.json",
        Trace.dumps(d),
    )
    return out


@router.get("/remind-me")
def remind_me(app: App = Depends(app_dep), user: User = Depends(current_user)):
    with app.vault(user).read() as conn:
        items = repo.list_items(conn, limit=1000)
    pool = [
        i
        for i in items
        if i["kind"] in ("challenge", "praise")
        or (i["kind"] in ("achievement", "soft_skill") and (i["impact"] or 0) >= 4)
    ]
    if len(pool) < 3:
        pool += [i for i in items if i["kind"] == "achievement" and i not in pool]
    picked, kinds = [], set()
    for i in pool:  # prefer variety: one of each kind first
        if i["kind"] not in kinds:
            picked.append(i)
            kinds.add(i["kind"])
        if len(picked) == 3:
            break
    for i in pool:
        if len(picked) == 3:
            break
        if i not in picked:
            picked.append(i)
    if not picked:
        return {
            "cards": [],
            "empty_message": "I don't have much yet. After a few reflections I'll "
            "be able to remind you of what you've gotten through.",
        }
    return {
        "cards": [
            {
                "item_id": i["id"],
                "kind": i["kind"],
                "title": i["title"],
                "occurred_on": i["occurred_on"],
                "text": i["bullet"] or i["result"] or i["what"],
            }
            for i in picked
        ],
        "empty_message": None,
    }
