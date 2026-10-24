"""Worker jobs (plan 11): finalize_session (then session_judge), reminder_tick,
cleanup_stale_sessions. Every job is idempotent. In Lambda: `handler`; locally the API runs them
in-process (DB-09).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from daybook.engine.app import OWNER, App, User, demo_user, get_app
from daybook.obs.log import log_event


def _user(event: dict) -> User:
    if event.get("sandbox_id"):
        return demo_user(event["sandbox_id"])
    return OWNER


async def run_job(app: App, event: dict[str, Any]) -> dict:
    job = event.get("job")
    now = datetime.now(UTC)
    if job == "finalize_session":
        from daybook.engine.finalize import finalize_session

        return await finalize_session(app, _user(event), event["session_id"])
    if job == "session_judge":
        return await session_judge(app, _user(event), event["session_id"])
    if job == "reminder_tick":
        out = {"cleanup": await cleanup_stale_sessions(app, now)}
        out["reminder"] = reminder_tick(app, now)
        return out
    if job == "cleanup_stale_sessions":
        return await cleanup_stale_sessions(app, now)
    raise ValueError(f"unknown job {job!r}")


async def session_judge(app: App, user: User, sid: str) -> dict:
    from daybook.agents.judge import flagged, judge
    from daybook.engine import state as st
    from daybook.obs.tracer import Trace

    loaded = st.load(app.store, user.prefix, sid)
    if loaded is None:
        return {"status": "missing"}
    state, _ = loaded
    if state.flags.get("judged") or not [t for t in state.turns if t.role == "user"]:
        return {"status": "skipped"}
    trace = Trace("judge", session_id=sid, job="session_judge")
    scores = await judge(app.deps, trace, state.turn_dicts())
    app.spend.add(user.role, trace.cost_usd, datetime.now(UTC))
    if scores is None:
        return {"status": "failed"}
    s = scores.model_dump()
    s["flagged"] = flagged(s)

    def write(conn):
        row = conn.execute("SELECT stats FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row:
            stats = json.loads(row["stats"])
            stats["judge"] = s
            stats["cost_usd"] = round(stats.get("cost_usd", 0) + trace.cost_usd, 6)
            conn.execute("UPDATE sessions SET stats = ? WHERE id = ?", (json.dumps(stats), sid))

    app.vault(user).write(write)
    app.store.put(
        f"traces/{user.uid}/{state.local_date}/jobs/judge-{sid}.json",
        Trace.dumps(trace.finish(scores=s)),
    )
    for _ in range(3):  # mark judged in the session file
        loaded = st.load(app.store, user.prefix, sid)
        state, etag = loaded
        state.flags["judged"] = True
        try:
            st.save(app.store, user.prefix, state, etag)
            break
        except Exception:
            continue
    return {"status": "judged", **s}


IDLE_HIDDEN_SEC = 120  # a hidden page with no new turn for this long ends the session (DB-13)
STALE_SEC = 15 * 60


async def cleanup_stale_sessions(app: App, now: datetime) -> dict:
    """End sessions that were left open: hidden for 2 min, or older than limit + 15 min. Also
    retries finalizes that were queued by the spend breaker or that failed."""
    from daybook.engine import conductor
    from daybook.engine import state as st
    from daybook.engine.finalize import finalize_session

    ended, retried = 0, 0
    prefixes = ["users/owner/"] + sorted(
        {
            "/".join(k.split("/")[:2]) + "/"
            for k in app.store.list("demo/")
            if k.endswith("active.json")
        }
    )
    for prefix in prefixes:
        user = OWNER if prefix == OWNER.prefix else demo_user(prefix.split("/")[1])
        sid = conductor.active_pointer(app, user)
        if sid:
            loaded = st.load(app.store, prefix, sid)
            if loaded and loaded[0].status == "active":
                s = loaded[0]
                hidden = s.flags.get("hidden_at")
                last = datetime.fromisoformat(s.turns[-1].created_at if s.turns else s.started_at)
                idle = (
                    bool(hidden)
                    and datetime.fromisoformat(hidden) >= last
                    and (now - datetime.fromisoformat(hidden)).total_seconds() > IDLE_HIDDEN_SEC
                )
                if idle or s.elapsed(now) > s.limit_sec + STALE_SEC:
                    await conductor.end_session(app, user, sid, "idle", now)
                    ended += 1
        if user.role == "owner":
            for key in app.store.list(f"{prefix}sessions/")[-20:]:
                loaded = st.load(app.store, prefix, key.rsplit("/", 1)[1][:-5])
                if (
                    loaded
                    and (loaded[0].flags.get("finalize_queued") or loaded[0].status == "failed")
                    and app.spend.status("owner", now) == "ok"
                ):
                    await finalize_session(app, user, loaded[0].id)
                    retried += 1
    return {"ended": ended, "retried": retried}


def reminder_tick(app: App, now: datetime) -> dict:
    from daybook.push.reminders import tick
    from daybook.push.webpush import VapidSender

    if not app.settings.vapid_private_key:
        return {"sent": 0, "reason": "no VAPID key"}
    sender = VapidSender(app.settings.vapid_private_key, app.settings.site_url)
    return tick(app.vault(OWNER), app.store, OWNER.prefix, sender, now)


def handler(event, context=None):  # Lambda entry point
    out = asyncio.run(run_job(get_app(), event))
    log_event(event="job_done", job=event.get("job"))
    return out
