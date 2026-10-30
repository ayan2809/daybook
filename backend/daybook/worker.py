"""Worker jobs (plan 11): finalize_session (then session_judge), reminder_tick,
cleanup_stale_sessions (demo sandboxes; accounts are sealed, DB-32). Every job is idempotent. In Lambda: `handler`; locally the API runs them
in-process (DB-09).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from daybook.engine.app import OWNER, App, User, account_user, demo_user, get_app
from daybook.obs.log import log_event


def job_identity(app: App, user: User) -> dict:
    """Who a background job runs as. An account's vault key rides along encrypted with a server
    key (DB-32): Lambda's async queue isn't readable from the account, and the worker never logs
    its event."""
    if user.role == "demo":
        return {"sandbox_id": user.sandbox_id}
    if user.key is None:  # offline tools: the unsealed owner
        return {"uid": user.uid}
    from daybook.storage.sealed import server_key, wrap

    k = wrap(server_key(app.settings.session_signing_key, "worker"), user.key, f"job:{user.aid}")
    return {"role": user.role, "aid": user.aid, "k": k}


def _user(app: App, event: dict) -> User:
    if event.get("sandbox_id"):
        return demo_user(event["sandbox_id"])
    if event.get("k"):
        from daybook.storage.sealed import server_key, unwrap

        wk = server_key(app.settings.session_signing_key, "worker")
        key = unwrap(wk, event["k"], f"job:{event['aid']}")
        return account_user(event.get("role") or "user", event["aid"], key)
    return OWNER


async def run_job(app: App, event: dict[str, Any]) -> dict:
    job = event.get("job")
    now = datetime.now(UTC)
    if job == "finalize_session":
        from daybook.engine.finalize import finalize_session

        return await finalize_session(app, _user(app, event), event["session_id"])
    if job == "session_judge":
        return await session_judge(app, _user(app, event), event["session_id"])
    if job == "reminder_tick":  # a schedule names its folder; the local ticker checks them all
        from daybook.push import notify

        if event.get("uid"):
            one = notify.prefix_for(event["uid"])
            prefixes = [one] if one else []
        else:
            prefixes = notify.prefixes(app)
        out = {"cleanup": await cleanup_stale_sessions(app, now)}
        out["reminder"] = {p.split("/")[1]: reminder_tick(app, p, now) for p in prefixes}
        return out
    if job == "cleanup_stale_sessions":
        return await cleanup_stale_sessions(app, now)
    raise ValueError(f"unknown job {job!r}")


async def session_judge(app: App, user: User, sid: str) -> dict:
    from daybook.agents.judge import flagged, judge
    from daybook.engine import state as st
    from daybook.obs.tracer import Trace

    loaded = st.load(app.store_for(user), user.prefix, sid)
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
    app.store_for(user).put(
        f"{user.prefix}traces/{state.local_date}/jobs/judge-{sid}.json",
        Trace.dumps(trace.finish(scores=s)),
    )
    for _ in range(3):  # mark judged in the session file
        loaded = st.load(app.store_for(user), user.prefix, sid)
        state, etag = loaded
        state.flags["judged"] = True
        try:
            st.save(app.store_for(user), user.prefix, state, etag)
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
    """End demo sessions that were left open: hidden for 2 min, or older than limit + 15 min. An
    account's sessions are sealed with a key the worker doesn't have, so those are ended (and
    queued finalizes retried) when the user next opens Today (DB-32)."""
    from daybook.engine import conductor
    from daybook.engine import state as st

    ended = 0
    demos = sorted({k.split("/")[1] for k in app.store.list("demo/") if k.endswith("active.json")})
    for user in [demo_user(sb) for sb in demos]:
        sid = conductor.active_pointer(app, user)
        if sid:
            loaded = st.load(app.store, user.prefix, sid)
            if loaded and loaded[0].status == "active" and is_stale(loaded[0], now):
                await conductor.end_session(app, user, sid, "idle", now)
                ended += 1
    return {"ended": ended}


async def retry_finalizes(app: App, user: User, now: datetime, recent: int = 3) -> int:
    """Queued (spend breaker) or failed finalizes among the last few sessions: run them again."""
    from daybook.engine import state as st

    if not user.is_account or app.spend.status(user, now) != "ok":
        return 0
    n = 0
    store = app.store_for(user)
    for key in app.store.list(f"{user.prefix}sessions/")[-recent:]:
        loaded = st.load(store, user.prefix, key.rsplit("/", 1)[1][:-5])
        if loaded and (loaded[0].flags.get("finalize_queued") or loaded[0].status == "failed"):
            app.dispatch("finalize_session", session_id=loaded[0].id, **job_identity(app, user))
            n += 1
    return n


def reminder_tick(app: App, prefix: str, now: datetime) -> dict:
    from daybook.push.reminders import tick
    from daybook.push.webpush import VapidSender

    if not app.settings.vapid_private_key:
        return {"sent": 0, "reason": "no VAPID key"}
    sender = VapidSender(app.settings.vapid_private_key, app.settings.site_url)
    return tick(app, prefix, sender, now)


def handler(event, context=None):  # Lambda entry point
    out = asyncio.run(run_job(get_app(), event))
    log_event(event="job_done", job=event.get("job"))
    return out
