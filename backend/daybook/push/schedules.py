"""Reminder schedules in EventBridge Scheduler (prod, DB-27, DB-29).

Each account gets two schedules (first and second nudge) named `daybook-reminder-<uid>-first|second`
that invoke the worker's `reminder_tick` for that account at the exact reminder times, so nothing
wakes up every 15 minutes. Saving Settings creates or moves them; deleting the account removes them.
UpdateSchedule replaces the whole schedule, so every call sends the full definition.
"""

from __future__ import annotations

import json
from typing import Any

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]  # Settings days: 1 = Mon … 7 = Sun
NUDGES = ("first", "second")


def name_for(uid: str, nudge: str) -> str:
    return f"daybook-reminder-{uid}-{nudge}"


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


def _client(client: Any):
    if client is not None:
        return client
    import boto3

    return boto3.client("scheduler")


def sync_reminder_schedules(
    uid: str,
    reminder: dict,
    tz: str,
    *,
    worker_arn: str,
    role_arn: str,
    client: Any = None,
) -> list[dict]:
    client = _client(client)
    out = []
    for nudge, (expr, enabled) in zip(NUDGES, wanted(reminder), strict=True):
        name = name_for(uid, nudge)
        spec = {
            "Name": name,
            "Description": f"Daybook {nudge} reflection nudge",
            "ScheduleExpression": expr,
            "ScheduleExpressionTimezone": tz,
            "State": "ENABLED" if enabled else "DISABLED",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": worker_arn,
                "RoleArn": role_arn,
                "Input": json.dumps({"job": "reminder_tick", "uid": uid}),
                "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 600},
            },
        }
        try:
            client.update_schedule(**spec)
        except client.exceptions.ResourceNotFoundException:
            if not enabled:  # nothing to switch off
                continue
            client.create_schedule(**spec)
        out.append({"name": name, "expression": expr, "state": spec["State"]})
    return out


def delete_reminder_schedules(uid: str, client: Any = None) -> None:
    client = _client(client)
    for nudge in NUDGES:
        try:
            client.delete_schedule(Name=name_for(uid, nudge))
        except client.exceptions.ResourceNotFoundException:
            pass
