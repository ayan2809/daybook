"""The reminder tick (plan 12.3): every 15 minutes, send the evening reminder when it's due.

Due = reminders enabled, today is a reminder day, the time is inside the current 15-minute window,
nothing was reflected today, and that nudge wasn't sent yet today. Two nudges by default (19:00,
21:00). A 404/410 from the push service deletes the subscription.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from daybook.memory import repo
from daybook.push.webpush import Sender

WINDOW_MIN = 15
DEFAULT_REMINDER = {"enabled": False, "time": "19:00", "days": [1, 2, 3, 4, 5],
                    "second_nudge": True, "second_time": "21:00"}


def _in_window(now_local: datetime, hhmm: str) -> bool:
    h, m = (int(x) for x in hhmm.split(":"))
    due = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    return due <= now_local < due + timedelta(minutes=WINDOW_MIN)


def body_for(n_pending: int) -> str:
    if n_pending == 0:
        return "How did today go? Five minutes, by voice."
    what = "number" if n_pending == 1 else "numbers"
    return f"How did today go? {n_pending} {what} to confirm from yesterday."


def tick(vault, store, prefix: str, sender: Sender, now: datetime) -> dict:
    """Returns {"sent": n, "nudge": "first"|"second"|None, "reason": ...}."""
    with vault.read() as conn:
        prefs = repo.get_setting(conn, "app_settings", {}) or {}
        profile = repo.get_profile(conn) or {}
        tz = profile.get("timezone") or "Asia/Kolkata"
        now_local = now.astimezone(ZoneInfo(tz))
        today = now_local.date().isoformat()
        rem = {**DEFAULT_REMINDER, **(prefs.get("reminder") or {})}
        reflected = today in repo.reflected_dates(conn, today)
        n_pending = len(repo.open_pending(conn, today))
        subs = repo.rows(conn.execute("SELECT * FROM push_subscriptions"))
    if not rem["enabled"]:
        return {"sent": 0, "nudge": None, "reason": "disabled"}
    if now_local.isoweekday() not in rem["days"]:
        return {"sent": 0, "nudge": None, "reason": "not a reminder day"}
    nudge = None
    if _in_window(now_local, rem["time"]):
        nudge = "first"
    elif rem.get("second_nudge") and _in_window(now_local, rem.get("second_time", "21:00")):
        nudge = "second"
    if nudge is None:
        return {"sent": 0, "nudge": None, "reason": "not due"}
    if reflected:
        return {"sent": 0, "nudge": nudge, "reason": "already reflected"}
    log_key = f"{prefix}reminder-log/{today}.json"
    b = store.get(log_key)
    log = json.loads(b.data) if b else {}
    if log.get(nudge):
        return {"sent": 0, "nudge": nudge, "reason": "already sent"}
    payload = {"title": "Daybook", "body": body_for(n_pending),
               "url": "/reflect?mode=guided&src=push"}
    sent, gone = 0, []
    for s in subs:
        r = sender.send(s, payload)
        if r.ok:
            sent += 1
        elif r.gone:
            gone.append(s["id"])
    if gone or sent:
        def write(conn):
            for sid in gone:
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sid,))
            if sent:
                conn.execute("UPDATE push_subscriptions SET last_success_at = ?", (repo.now_iso(),))
        vault.write(write)
    log[nudge] = repo.now_iso()
    store.put(log_key, json.dumps(log).encode())
    return {"sent": sent, "nudge": nudge, "reason": "sent", "deleted": len(gone)}
