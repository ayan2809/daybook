"""Today, sessions, turns, audio, summary, feedback (plan 5.3, 11)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from daybook.api.deps import ApiError, app_dep, current_user, now_dep, rate_limit
from daybook.engine import conductor, demo
from daybook.engine import state as st
from daybook.engine.app import App, User
from daybook.engine.conductor import SessionError
from daybook.memory import repo
from daybook.obs.log import log_event
from daybook.voice import tts as tts_mod
from daybook.worker import is_stale, retry_finalizes

router = APIRouter(dependencies=[Depends(rate_limit)])


def _wrap(exc: SessionError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, **exc.extra)


def _suggest(user: User, view: dict) -> list[str]:
    if user.role != "demo" or view.get("status") != "active":
        return []
    return demo.suggestions_for(view["phase"], view.get("pending_action"))


@router.get("/today")
async def today(
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    with app.vault(user).read() as conn:
        prof = repo.get_profile(conn) or {}
        tz = prof.get("timezone") or app.settings.tz_default
        d = now.astimezone(ZoneInfo(tz)).date()
        start = d - timedelta(days=13)
        done = repo.reflected_dates(conn, start.isoformat())
        pending = repo.open_pending(conn, d.isoformat())
        last = conn.execute(
            "SELECT d.period_start, d.text, s.id FROM digests d LEFT JOIN sessions s"
            " ON s.local_date = d.period_start WHERE d.kind = 'day' ORDER BY d.period_start DESC"
            " LIMIT 1"
        ).fetchone()
    active = conductor.active_pointer(app, user)
    if active:
        loaded = st.load(app.store_for(user), user.prefix, active)
        if not loaded or loaded[0].status != "active":
            active = None
        elif is_stale(loaded[0], now):  # no 15-minute sweep in prod: clean up on open (DB-27)
            await conductor.end_session(app, user, active, "idle", now)
            active = None
    if user.is_account and not active:  # the worker can't open a sealed vault on its own (DB-32)
        await retry_finalizes(app, user, now)
    # sessions finished today but not yet in the vault count as reflected too
    reflected = d.isoformat() in done
    return {
        "date": d.isoformat(),
        "weekday": d.strftime("%A"),
        "display_name": prof.get("display_name"),
        "reflected_today": reflected,
        "active_session_id": active,
        "streak": [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "reflected": (start + timedelta(days=i)).isoformat() in done,
            }
            for i in range(14)
        ],
        "pending": [_pending(p) for p in pending],
        "last_summary": {"date": last[0], "text": last[1], "session_id": last[2]} if last else None,
    }


def _pending(p: dict) -> dict:
    return {
        k: p.get(k)
        for k in (
            "id",
            "item_id",
            "item_title",
            "kind",
            "question",
            "status",
            "times_asked",
            "created_at",
            "snooze_until",
        )
    }


class StartBody(BaseModel):
    mode: str = Field(pattern="^(guided|dump|quick|edit)$")
    item_id: int | None = None


@router.post("/sessions", status_code=201)
async def start(
    body: StartBody,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    if body.mode == "edit" and not body.item_id:
        raise ApiError(422, "validation", "item_id is required for edit mode.")
    if user.role == "demo":
        active = conductor.active_pointer(app, user)
        if not active and (
            app.spend.status("demo", now) != "ok"
            or not app.demo_caps.take(user.sandbox_id, "sessions", now)
        ):
            raise ApiError(429, "demo_cap", "The demo's daily limit was reached. Here's a replay.")
    if user.role == "user" and not conductor.active_pointer(app, user):
        if app.spend.status(user, now) != "ok":  # per-account and all-accounts caps (DB-29)
            raise ApiError(429, "budget", "You've reached today's limit. Come back tomorrow.")
    try:
        state = await conductor.start_session(app, user, body.mode, now, body.item_id)
    except SessionError as exc:
        raise _wrap(exc) from None
    view = conductor.session_view(state, now)
    view["suggestions"] = _suggest(user, view)
    return {"session": view}


@router.get("/sessions/{sid}")
def get_session(
    sid: str,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    loaded = st.load(app.store_for(user), user.prefix, sid)
    if not loaded:
        raise ApiError(404, "not_found", "No such session.")
    view = conductor.session_view(loaded[0], now)
    view["suggestions"] = _suggest(user, view)
    return {"session": view}


class TurnBody(BaseModel):
    client_turn_id: str = Field(min_length=8, max_length=64)
    text: str = Field(min_length=1, max_length=4000)
    dictated_chars: int = Field(default=0, ge=0)


@router.post("/sessions/{sid}/turns")
async def post_turn(
    sid: str,
    body: TurnBody,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    try:
        res = await conductor.handle_turn(
            app, user, sid, body.client_turn_id, body.text, body.dictated_chars, now
        )
    except SessionError as exc:
        raise _wrap(exc) from None
    res = dict(res)
    res["suggestions"] = _suggest(
        user,
        {
            "status": "ended" if res["ended"] else "active",
            "phase": res["phase"],
            "pending_action": res["pending_action"],
        },
    )
    return res


@router.get("/turns/{turn_id}/audio")
async def audio(
    turn_id: str,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    sid, _, seq = turn_id.partition(".")
    loaded = st.load(app.store_for(user), user.prefix, sid)
    if not loaded or not seq.isdigit():
        raise ApiError(404, "not_found", "No such turn.")
    state, etag = loaded
    turn = next((t for t in state.turns if t.seq == int(seq) and t.role == "assistant"), None)
    if turn is None:
        raise ApiError(404, "not_found", "No such turn.")
    if app.tts is None or (not state.voice_enabled and not turn.safety):
        return Response(status_code=204)
    if app.spend.status(user, now) != "ok" and not turn.safety:
        return Response(status_code=204)
    if (
        state.budgets.get("tts_chars", 0) + len(turn.text) > state.tts_char_limit
        and not turn.safety
    ):
        _update(app, user, sid, lambda s: setattr(s, "voice_enabled", False))
        return Response(status_code=204)
    try:
        own = user.prefix if user.is_account else ""  # an account's audio stays in its folder
        a = await tts_mod.speak(app.tts, app.store_for(user), turn.text, own)
    except Exception as exc:
        log_event(event="tts_failed", session_id=sid, error=type(exc).__name__)

        def fail(s):
            s.tts_failures += 1
            if s.tts_failures >= 2:
                s.voice_enabled = False

        _update(app, user, sid, fail)
        raise ApiError(503, "unavailable", "Voice unavailable.") from None
    if not a.cached:
        app.spend.add(user, a.cost_usd, now)

        def count(s):
            s.budgets["tts_chars"] = s.budgets.get("tts_chars", 0) + len(turn.text)
            s.budgets["cost_usd"] = round(s.budgets.get("cost_usd", 0) + a.cost_usd, 6)

        _update(app, user, sid, count)
    return Response(a.data, media_type=a.mime, headers={"Cache-Control": "private, max-age=3600"})


def _update(app: App, user: User, sid: str, fn) -> None:
    for _ in range(3):
        loaded = st.load(app.store_for(user), user.prefix, sid)
        if not loaded:
            return
        s, etag = loaded
        fn(s)
        try:
            st.save(app.store_for(user), user.prefix, s, etag)
            return
        except Exception:
            continue


@router.post("/sessions/{sid}/end", status_code=202)
async def end(
    sid: str,
    request: Request,
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    reason = "user"
    try:
        body = await request.json()
        reason = body.get("reason", "user") if isinstance(body, dict) else "user"
    except Exception:
        raw = (await request.body()).decode(errors="ignore")
        reason = "idle" if "idle" in raw or "hidden" in raw else "user"
    loaded = st.load(app.store_for(user), user.prefix, sid)
    if not loaded:
        raise ApiError(404, "not_found", "No such session.")
    if reason in ("idle", "hidden", "pagehide"):
        # soft signal (DB-13): mark hidden; cleanup ends it if no turn follows within 2 minutes
        if loaded[0].status == "active":
            _update(app, user, sid, lambda s: s.flags.__setitem__("hidden_at", conductor._iso(now)))
        return {"status": loaded[0].status}
    try:
        state = await conductor.end_session(app, user, sid, "user", now)
    except SessionError as exc:
        raise _wrap(exc) from None
    return {"status": state.status}


@router.get("/sessions/{sid}/summary")
def summary(sid: str, app: App = Depends(app_dep), user: User = Depends(current_user)):
    loaded = st.load(app.store_for(user), user.prefix, sid)
    if not loaded:
        raise ApiError(404, "not_found", "No such session.")
    s = loaded[0]
    from daybook.engine.finalize import session_stats

    stats = session_stats(s)
    out = {
        "session_id": s.id,
        "status": "ended" if s.status == "active" else s.status,
        "date": s.local_date,
        "day_summary": None,
        "items": [],
        "pending_created": [],
        "stats": {
            "duration_sec": stats["duration_sec"],
            "turns": stats["turns"],
            "spoken_pct": stats["spoken_pct"],
        },
        "feedback": s.feedback,
    }
    if s.status == "finalized" and s.summary:
        with app.vault(user).read() as conn:
            items = repo.list_items(conn, ids=s.summary["item_ids"], include_hidden=True)
            pend = [repo.get_pending(conn, p) for p in s.summary["pending_ids"]]
        out.update(
            day_summary=s.summary["day_summary"],
            items=items,
            pending_created=[_pending(p) for p in pend if p],
        )
    return out


class FeedbackBody(BaseModel):
    value: str = Field(pattern="^(up|down)$")


@router.post("/sessions/{sid}/feedback")
def feedback(
    sid: str, body: FeedbackBody, app: App = Depends(app_dep), user: User = Depends(current_user)
):
    if not st.load(app.store_for(user), user.prefix, sid):
        raise ApiError(404, "not_found", "No such session.")
    _update(app, user, sid, lambda s: setattr(s, "feedback", body.value))
    app.vault(user).write(
        lambda c: c.execute("UPDATE sessions SET feedback = ? WHERE id = ?", (body.value, sid))
    )
    return {"ok": True}


def _today(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()
