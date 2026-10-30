"""The reminder tick (plan 12.3, DB-27, DB-32): send the evening reminder when it's due.

Due = reminders enabled, today is a reminder day, the time is inside the current 15-minute window,
nothing was reflected today, and that nudge wasn't sent yet today. Two nudges by default (19:00,
21:00). A 404/410 from the push service deletes the subscription.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from daybook.memory import repo
from daybook.push import notify
from daybook.push.webpush import Sender

WINDOW_MIN = 15
DEFAULT_REMINDER = {
    "enabled": False,
    "time": "19:00",
    "days": [1, 2, 3, 4, 5],
    "second_nudge": True,
    "second_time": "21:00",
}


def _in_window(now_local: datetime, hhmm: str) -> bool:
    h, m = (int(x) for x in hhmm.split(":"))
    due = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    return due <= now_local < due + timedelta(minutes=WINDOW_MIN)


def body_for(n_pending: int) -> str:
    if n_pending == 0:
        return "How did today go? Five minutes, by voice."
    what = "number" if n_pending == 1 else "numbers"
    return f"How did today go? {n_pending} {what} to confirm from yesterday."


def tick(app, prefix: str, sender: Sender, now: datetime) -> dict:
    """Returns {"sent": n, "nudge": "first"|"second"|None, "reason": ...}. Reads only the account's
    reminder record (DB-32): a reminder fires while the user, and their key, are away."""
    rec = notify.load(app, prefix)
    tz = rec.get("tz") or "Asia/Kolkata"
    now_local = now.astimezone(ZoneInfo(tz))
    today = now_local.date().isoformat()
    rem = {**DEFAULT_REMINDER, **(rec.get("reminder") or {})}
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
    if rec.get("reflected") == today:
        return {"sent": 0, "nudge": nudge, "reason": "already reflected"}
    if (rec.get("sent") or {}).get("date") == today and rec["sent"].get(nudge):
        return {"sent": 0, "nudge": nudge, "reason": "already sent"}
    payload = {
        "title": "Daybook",
        "body": body_for(int(rec.get("pending") or 0)),
        "url": "/reflect?mode=guided&src=push",
    }
    sent, gone = 0, []
    for s in rec.get("subs") or []:
        r = sender.send(s, payload)
        if r.ok:
            sent += 1
        elif r.gone:
            gone.append(s["endpoint"])

    def write(r: dict) -> None:
        r["subs"] = [s for s in r.get("subs") or [] if s["endpoint"] not in gone]
        log = r.get("sent") if (r.get("sent") or {}).get("date") == today else {"date": today}
        log[nudge] = repo.now_iso()
        r["sent"] = log

    notify.update(app, prefix, write)
    return {"sent": sent, "nudge": nudge, "reason": "sent", "deleted": len(gone)}
