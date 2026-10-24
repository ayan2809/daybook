"""The $2/day spend breaker (plan 15.1) and demo caps.

`spend/{YYYY-MM-DD}.json` (IST day) holds {total, demo, owner}, updated with If-Match and retried on
conflict. Counts come from token estimates (obs/pricing.yaml).
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from daybook.storage.blob import BlobStore, PreconditionFailed

IST = ZoneInfo("Asia/Kolkata")


def ist_day(now: datetime) -> str:
    return now.astimezone(IST).date().isoformat()


class Counter:
    """A small JSON counter file with conditional writes."""

    def __init__(self, store: BlobStore, key: str):
        self.store = store
        self.key = key

    def read(self) -> dict:
        b = self.store.get(self.key)
        return json.loads(b.data) if b else {}

    def add(self, **inc: float) -> dict:
        for _ in range(5):
            b = self.store.get(self.key)
            cur = json.loads(b.data) if b else {}
            for k, v in inc.items():
                cur[k] = round(cur.get(k, 0) + v, 6)
            try:
                self.store.put(
                    self.key,
                    json.dumps(cur).encode(),
                    if_match=b.etag if b else None,
                    if_none_match=b is None,
                    content_type="application/json",
                )
                return cur
            except PreconditionFailed:
                continue
        return cur


class SpendBreaker:
    def __init__(self, store: BlobStore, daily_budget: float, demo_budget: float):
        self.store = store
        self.daily = daily_budget
        self.demo = demo_budget

    def _counter(self, now: datetime) -> Counter:
        return Counter(self.store, f"spend/{ist_day(now)}.json")

    def today(self, now: datetime) -> dict:
        d = self._counter(now).read()
        return {
            "total": d.get("total", 0.0),
            "demo": d.get("demo", 0.0),
            "owner": d.get("owner", 0.0),
            "budget": self.daily,
        }

    def add(self, role: str, cost: float, now: datetime) -> None:
        if cost > 0:
            self._counter(now).add(total=cost, **{role: cost})

    def status(self, role: str, now: datetime, estimate: float = 0.0) -> str:
        """ok | demo_replay | over"""
        d = self.today(now)
        if d["total"] + estimate >= self.daily:
            return "demo_replay" if role == "demo" else "over"
        if role == "demo" and d["demo"] + estimate >= self.demo:
            return "demo_replay"
        return "ok"


class DemoCaps:
    """Daily global caps and per-sandbox caps for the public demo (plan 15)."""

    def __init__(self, store: BlobStore, settings):
        self.store = store
        self.s = settings

    def _day(self, now: datetime) -> Counter:
        return Counter(self.store, f"demo-caps/{ist_day(now)}.json")

    def _sandbox(self, sandbox_id: str) -> Counter:
        return Counter(self.store, f"demo/{sandbox_id}/caps.json")

    def usage(self, sandbox_id: str, now: datetime) -> dict:
        day, sb = self._day(now).read(), self._sandbox(sandbox_id).read()
        return {
            "sessions_left": max(
                0,
                min(
                    self.s.demo_sandbox_sessions - sb.get("sessions", 0),
                    self.s.demo_daily_sessions - day.get("sessions", 0),
                ),
            ),
            "asks_left": max(
                0,
                min(
                    self.s.demo_sandbox_asks - sb.get("asks", 0),
                    self.s.demo_daily_asks - day.get("asks", 0),
                ),
            ),
        }

    def take(self, sandbox_id: str, what: str, now: datetime) -> bool:
        """Use one session or ask. False when a cap is hit (→ replay mode)."""
        left = self.usage(sandbox_id, now)[f"{what}_left"]
        if left <= 0:
            return False
        self._day(now).add(**{what: 1})
        self._sandbox(sandbox_id).add(**{what: 1})
        return True
