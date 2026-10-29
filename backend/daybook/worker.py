"""Worker jobs (plan 11): finalize_session (then session_judge), reminder_tick,
cleanup_stale_sessions. Every job is idempotent. In Lambda: `handler`; locally the API runs them
in-process (DB-09).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from daybook.engine.app import OWNER, App, User, account_user, demo_user, get_app
from daybook.obs.log import log_event


def _user(event: dict) -> User:
    if event.get("sandbox_id"):
        return demo_user(event["sandbox_id"])
    return account_user(event.get("uid") or OWNER.uid)


def _accounts(app: App) -> list[User]:
    from daybook.engine import accounts

    return [OWNER] + [account_user(uid) for uid in accounts.all_uids(app)]


async def run_job(app: App, event: dict[str, Any]) -> dict:
    job = event.get("job")
    now = datetime.now(UTC)
    if job == "finalize_session":
        from daybook.engine.finalize import finalize_session

        return await finalize_session(app, _user(event), event["session_id"])
    if job == "session_judge":
        return await session_judge(app, _user(event), event["session_id"])
    if job == "reminder_tick":  # a schedule names its account; the local ticker checks them all
        users = [_user(event)] if event.get("uid") else _accounts(app)
        out = {"cleanup": await cleanup_stale_sessions(app, now)}
        out["reminder"] = {u.uid: reminder_tick(app, u, now) for u in users}
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
    app.spend.add(user, trace.cost_usd, datetime.now(UTC))
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


def is_stale(s, now: datetime) -> bool:
    """Hidden for 2 min with no newer turn, or older than its limit + 15 min."""
    hidden = s.flags.get("hidden_at")
    last = datetime.fromisoformat(s.turns[-1].created_at if s.turns else s.started_at)
    idle = (
        bool(hidden)
        and datetime.fromisoformat(hidden) >= last
        and (now - datetime.fromisoformat(hidden)).total_seconds() > IDLE_HIDDEN_SEC
    )
    return idle or s.elapsed(now) > s.limit_sec + STALE_SEC


async def cleanup_stale_sessions(app: App, now: datetime) -> dict:
    """End sessions that were left open: hidden for 2 min, or older than limit + 15 min. Also
    retries finalizes that were queued by the spend breaker or that failed."""
    from daybook.engine import conductor
    from daybook.engine import state as st
    from daybook.engine.finalize import finalize_session

    ended, retried = 0, 0
    demos = sorted({k.split("/")[1] for k in app.store.list("demo/") if k.endswith("active.json")})
    for user in _accounts(app) + [demo_user(sb) for sb in demos]:
        prefix = user.prefix
        sid = conductor.active_pointer(app, user)
        if sid:
            loaded = st.load(app.store, prefix, sid)
            if loaded and loaded[0].status == "active" and is_stale(loaded[0], now):
                await conductor.end_session(app, user, sid, "idle", now)
                ended += 1
        if user.is_account:
            for key in app.store.list(f"{prefix}sessions/")[-20:]:
                loaded = st.load(app.store, prefix, key.rsplit("/", 1)[1][:-5])
                if (
                    loaded
                    and (loaded[0].flags.get("finalize_queued") or loaded[0].status == "failed")
                    and app.spend.status(user, now) == "ok"
                ):
                    await finalize_session(app, user, loaded[0].id)
                    retried += 1
    return {"ended": ended, "retried": retried}


def reminder_tick(app: App, user: User, now: datetime) -> dict:
    from daybook.push.reminders import tick
    from daybook.push.webpush import VapidSender

    if not app.settings.vapid_private_key:
        return {"sent": 0, "reason": "no VAPID key"}
    sender = VapidSender(app.settings.vapid_private_key, app.settings.site_url)
    return tick(app.vault(user), app.store, user.prefix, sender, now)


def handler(event, context=None):  # Lambda entry point
    out = asyncio.run(run_job(get_app(), event))
    log_event(event="job_done", job=event.get("job"))
    return out
