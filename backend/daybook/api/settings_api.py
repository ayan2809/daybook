"""Settings, push subscriptions, exports and data deletion (plan 10, 11, 14.3)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from daybook.api.deps import COOKIE, ApiError, account_only, app_dep, current_user, now_dep
from daybook.engine import accounts
from daybook.engine.app import App, User
from daybook.memory import export, repo
from daybook.obs.log import log_event
from daybook.push import notify
from daybook.push.reminders import DEFAULT_REMINDER

router = APIRouter()
log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "reminder": {**DEFAULT_REMINDER, "enabled": False},
    "session_limit_sec": 420,
    "voice": True,
    "auto_send": True,
    "pending_at": "start",
    "keep_names": False,
}


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = (
            _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        )
    return out


def read_settings(app: App, user: User) -> dict:
    with app.vault(user).read() as conn:
        stored = repo.get_setting(conn, "app_settings", {}) or {}
        prof = repo.get_profile(conn) or {}
    s = _deep_merge(DEFAULTS, {k: v for k, v in stored.items() if k in DEFAULTS})
    s["profile"] = {
        "display_name": prof.get("display_name"),
        "org": prof.get("org"),
        "role_title": prof.get("role_title"),
        "timezone": prof.get("timezone") or app.settings.tz_default,
    }
    return s


@router.get("/settings")
def get_settings_(app: App = Depends(app_dep), user: User = Depends(current_user)):
    return read_settings(app, user)


@router.put("/settings")
def put_settings(body: dict, app: App = Depends(app_dep), user: User = Depends(current_user)):
    profile = body.pop("profile", None)
    if "session_limit_sec" in body and body["session_limit_sec"] not in (300, 360, 420):
        raise ApiError(422, "validation", "session_limit_sec must be 300, 360 or 420.")
    if "pending_at" in body and body["pending_at"] not in ("start", "end"):
        raise ApiError(422, "validation", "pending_at must be start or end.")
    rem = body.get("reminder") or {}
    for t in ("time", "second_time"):
        if t in rem:
            try:
                h, m = (int(x) for x in str(rem[t]).split(":"))
                assert 0 <= h < 24 and 0 <= m < 60
            except Exception:
                raise ApiError(422, "validation", f"reminder.{t} must be HH:MM.") from None
    if user.role == "demo":
        body.pop("reminder", None)

    def write(conn):
        cur = repo.get_setting(conn, "app_settings", {}) or {}
        repo.put_setting(
            conn, "app_settings", _deep_merge(cur, {k: v for k, v in body.items() if k in DEFAULTS})
        )
        if profile:
            if repo.get_profile(conn) is None:
                repo.upsert_profile(conn, timezone=app.settings.tz_default)
            repo.upsert_profile(
                conn,
                **{
                    k: profile.get(k)
                    for k in ("display_name", "org", "role_title", "timezone")
                    if k in profile
                },
            )

    app.vault(user).write(write)
    out = read_settings(app, user)
    notify.for_user(  # what a reminder needs while you're away (DB-32)
        app,
        user,
        lambda r: r.update(reminder=out["reminder"], tz=out["profile"]["timezone"]),
    )
    s = app.settings
    if s.scheduler_role_arn and s.worker_function_arn and user.is_account and (rem or profile):
        from daybook.push.schedules import sync_reminder_schedules

        try:
            moved = sync_reminder_schedules(
                user.uid,
                out["reminder"],
                out["profile"]["timezone"],
                worker_arn=s.worker_function_arn,
                role_arn=s.scheduler_role_arn,
            )
            log_event(event="reminder_schedules", schedules=moved)
        except Exception:  # settings are saved; reminder times stay as they were
            log.exception("reminder schedule sync failed")
    return out


class PushSub(BaseModel):
    endpoint: str
    keys: dict[str, str] | None = None


@router.post("/push/subscribe")
def push_subscribe(body: PushSub, app: App = Depends(app_dep), user: User = Depends(account_only)):
    if not body.keys or not body.keys.get("p256dh") or not body.keys.get("auth"):
        raise ApiError(422, "validation", "Subscription keys missing.")
    sub = {"endpoint": body.endpoint, "p256dh": body.keys["p256dh"], "auth": body.keys["auth"]}

    def write(r: dict) -> None:
        r["subs"] = [x for x in r.get("subs") or [] if x["endpoint"] != body.endpoint][-4:] + [sub]
        if not r.get("reminder"):  # first subscription: carry the current reminder times over
            cur = read_settings(app, user)
            r.update(reminder=cur["reminder"], tz=cur["profile"]["timezone"])

    notify.for_user(app, user, write)
    return {"ok": True}


@router.delete("/push/subscribe")
def push_unsubscribe(
    body: PushSub, app: App = Depends(app_dep), user: User = Depends(account_only)
):
    notify.for_user(
        app,
        user,
        lambda r: r.update(subs=[x for x in r.get("subs") or [] if x["endpoint"] != body.endpoint]),
    )
    return {"ok": True}


@router.post("/push/test")
def push_test(app: App = Depends(app_dep), user: User = Depends(account_only)):
    if not app.settings.vapid_private_key:
        raise ApiError(503, "unavailable", "Push isn't configured (no VAPID key).")
    from daybook.push.webpush import VapidSender

    sender = VapidSender(app.settings.vapid_private_key, app.settings.site_url)
    subs = notify.load(app, user.prefix).get("subs") or []
    sent = 0
    for s in subs:
        r = sender.send(
            s,
            {
                "title": "Daybook",
                "body": "Test reminder: notifications work.",
                "url": "/reflect?mode=guided&src=push",
            },
        )
        sent += int(r.ok)
    return {"sent": sent}


@router.get("/export/resume-as-code")
def export_rac(
    since: str | None = None, app: App = Depends(app_dep), user: User = Depends(account_only)
):
    with app.vault(user).read() as conn:
        return export.resume_as_code(conn, since)


@router.get("/export/markdown")
def export_md(app: App = Depends(app_dep), user: User = Depends(account_only)):
    with app.vault(user).read() as conn:
        md = export.markdown(conn)
    return PlainTextResponse(
        md,
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="daybook.md"'},
    )


@router.get("/export/brain")
def export_brain(
    app: App = Depends(app_dep),
    user: User = Depends(current_user),
    now: datetime = Depends(now_dep),
):
    """The whole memory as one Markdown file written for an LLM to read. The demo can use it too
    (its data is made up)."""
    with app.vault(user).read() as conn:
        md = export.brain(conn, now)
    return PlainTextResponse(
        md,
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="daybook-memory.md"'},
    )


@router.get("/export/sqlite")
def export_sqlite(app: App = Depends(app_dep), user: User = Depends(account_only)):
    return Response(
        app.vault(user).raw_bytes(),
        media_type="application/x-sqlite3",
        headers={"Content-Disposition": 'attachment; filename="daybook-memory.db"'},
    )


class DeleteBody(BaseModel):
    confirm: str


@router.post("/data/delete")
def delete_all(
    body: DeleteBody,
    response: Response,
    app: App = Depends(app_dep),
    user: User = Depends(account_only),
    now: datetime = Depends(now_dep),
):
    if body.confirm != "delete":
        raise ApiError(422, "validation", 'Type "delete" to confirm.')
    n = accounts.delete(app, user)
    if app.settings.scheduler_role_arn:
        from daybook.push.schedules import delete_reminder_schedules

        try:
            delete_reminder_schedules(user.uid)
        except Exception:
            log.exception("reminder schedule delete failed")
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True, "deleted": n}
