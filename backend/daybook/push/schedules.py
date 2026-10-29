"""Reminder schedules in EventBridge Scheduler (prod, DB-27).

The stack creates two schedules (first and second nudge) that invoke the worker's `reminder_tick`
at the exact reminder times, so nothing wakes up every 15 minutes. Saving Settings moves them.
UpdateSchedule replaces the whole schedule, so we read it first and send every field back.
"""

from __future__ import annotations

from typing import Any

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]  # Settings days: 1 = Mon … 7 = Sun
KEEP = (
    "FlexibleTimeWindow",
    "Target",
    "Description",
    "GroupName",
    "ActionAfterCompletion",
    "StartDate",
    "EndDate",
    "KmsKeyArn",
)


def cron_for(hhmm: str, days: list[int]) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    dow = ",".join(DOW[d - 1] for d in sorted(set(days)) if 1 <= d <= 7) or "MON-FRI"
    return f"cron({m} {h} ? * {dow} *)"


def wanted(reminder: dict) -> list[tuple[str, bool]]:
    """(expression, enabled) for the first and the second nudge."""
    on = bool(reminder.get("enabled")) and bool(reminder.get("days"))
    days = reminder.get("days") or []
    return [
        (cron_for(reminder.get("time", "19:00"), days), on),
        (
            cron_for(reminder.get("second_time", "21:00"), days),
            on and bool(reminder.get("second_nudge")),
        ),
    ]


def sync_reminder_schedules(
    names: list[str], reminder: dict, tz: str, client: Any = None
) -> list[dict]:
    if client is None:
        import boto3

        client = boto3.client("scheduler")
    out = []
    for name, (expr, enabled) in zip(names, wanted(reminder), strict=False):
        cur = client.get_schedule(Name=name)
        keep = {k: cur[k] for k in KEEP if cur.get(k) is not None}
        state = "ENABLED" if enabled else "DISABLED"
        client.update_schedule(
            Name=name,
            ScheduleExpression=expr,
            ScheduleExpressionTimezone=tz,
            State=state,
            **keep,
        )
        out.append({"name": name, "expression": expr, "state": state})
    return out
