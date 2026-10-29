"""A16 reminder tick and the stale/idle session cleanup (DB-13)."""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from daybook.engine import conductor
from daybook.engine import state as st
from daybook.engine.app import OWNER
from daybook.memory import repo
from daybook.push.reminders import tick
from daybook.push.webpush import FakeSender
from daybook.worker import cleanup_stale_sessions
from tests.helpers import at, make_app

IST = ZoneInfo("Asia/Kolkata")


def _setup(app, enabled=True):
    def w(conn):
        repo.upsert_profile(conn, display_name="Ayan", timezone="Asia/Kolkata")
        repo.put_setting(conn, "app_settings", {"reminder": {"enabled": enabled}})
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)"
            " VALUES ('https://push/ok', 'k', 'a', 'now'), ('https://push/gone', 'k', 'a', 'now')"
        )
        iid = repo.create_item(
            conn,
            dict(
                kind="achievement", occurred_on="2026-10-26", title="X", what="x", source_quote="x"
            ),
        )
        repo.create_pending(conn, iid, "metric", "How many?")

    app.vault(OWNER).write(w)


def test_reminder_tick(tmp_path):
    app = make_app(tmp_path)
    _setup(app)
    sender = FakeSender()
    sender.gone_endpoints.add("https://push/gone")
    v = app.vault(OWNER)
    tue_1905 = datetime(2026, 10, 27, 19, 5, tzinfo=IST)
    assert (
        tick(v, app.store, OWNER.prefix, sender, tue_1905 - timedelta(hours=1))["reason"]
        == "not due"
    )
    r = tick(v, app.store, OWNER.prefix, sender, tue_1905)
    assert r == {"sent": 1, "nudge": "first", "reason": "sent", "deleted": 1}
    assert "1 number to confirm" in sender.sent[0][1]["body"]
    assert tick(v, app.store, OWNER.prefix, sender, tue_1905)["reason"] == "already sent"
    with v.read() as conn:
        assert conn.execute("SELECT count(*) FROM push_subscriptions").fetchone()[0] == 1
    r = tick(v, app.store, OWNER.prefix, sender, tue_1905.replace(hour=21))
    assert r["nudge"] == "second" and r["sent"] == 1
    sat = datetime(2026, 10, 31, 19, 5, tzinfo=IST)
    assert tick(v, app.store, OWNER.prefix, sender, sat)["reason"] == "not a reminder day"


def test_no_reminder_after_reflecting(tmp_path):
    app = make_app(tmp_path)
    _setup(app)
    app.vault(OWNER).write(
        lambda c: repo.upsert_session(
            c,
            {
                "id": "s1",
                "mode": "guided",
                "local_date": "2026-10-27",
                "started_at": "x",
                "status": "finalized",
                "turn_count": 3,
            },
        )
    )
    r = tick(
        app.vault(OWNER),
        app.store,
        OWNER.prefix,
        FakeSender(),
        datetime(2026, 10, 27, 19, 5, tzinfo=IST),
    )
    assert r["reason"] == "already reflected"


async def test_hidden_session_ends_after_two_minutes_unless_a_turn_follows(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    loaded, etag = st.load(app.store, OWNER.prefix, s.id)
    loaded.flags["hidden_at"] = conductor._iso(at(10))
    st.save(app.store, OWNER.prefix, loaded, etag)
    assert (await cleanup_stale_sessions(app, at(60)))["ended"] == 0  # under 2 min
    # a turn arrives (the Wispr round trip came back): the hidden mark no longer counts
    await conductor.handle_turn(app, OWNER, s.id, uuid.uuid4().hex, "I fixed the build.", 0, at(70))
    assert (await cleanup_stale_sessions(app, at(300)))["ended"] == 0
    loaded, etag = st.load(app.store, OWNER.prefix, s.id)
    loaded.flags["hidden_at"] = conductor._iso(at(310))
    st.save(app.store, OWNER.prefix, loaded, etag)
    assert (await cleanup_stale_sessions(app, at(500)))["ended"] == 1
    await app.drain()
    assert st.load(app.store, OWNER.prefix, s.id)[0].status == "finalized"


# DB-27, DB-29: exact per-account reminder schedules instead of a 15-minute tick
class FakeScheduler:
    class exceptions:  # noqa: N801 (mirrors boto3's client.exceptions)
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self):
        self.schedules: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def update_schedule(self, **kw):
        if kw["Name"] not in self.schedules:
            raise self.exceptions.ResourceNotFoundException(kw["Name"])
        self.calls.append(("update", kw["Name"]))
        self.schedules[kw["Name"]] = kw

    def create_schedule(self, **kw):
        self.calls.append(("create", kw["Name"]))
        self.schedules[kw["Name"]] = kw

    def delete_schedule(self, Name):
        if Name not in self.schedules:
            raise self.exceptions.ResourceNotFoundException(Name)
        self.calls.append(("delete", Name))
        del self.schedules[Name]


def test_reminder_schedules():
    from daybook.push.schedules import cron_for, delete_reminder_schedules, sync_reminder_schedules

    assert cron_for("19:00", [1, 2, 3, 4, 5]) == "cron(0 19 ? * MON,TUE,WED,THU,FRI *)"
    assert cron_for("07:30", [7, 6, 6]) == "cron(30 7 ? * SAT,SUN *)"
    fake = FakeScheduler()
    arns = {"worker_arn": "arn:worker", "role_arn": "arn:role"}
    rem = {"enabled": True, "time": "18:45", "days": [1, 3], "second_nudge": False}
    out = sync_reminder_schedules("gabc", rem, "Asia/Kolkata", client=fake, **arns)
    # the first nudge is created; the disabled second one isn't created just to be switched off
    assert fake.calls == [("create", "daybook-reminder-gabc-first")]
    assert [o["state"] for o in out] == ["ENABLED"]
    first = fake.schedules["daybook-reminder-gabc-first"]
    assert first["ScheduleExpression"] == "cron(45 18 ? * MON,WED *)"
    assert first["ScheduleExpressionTimezone"] == "Asia/Kolkata"
    assert first["Target"]["Input"] == '{"job": "reminder_tick", "uid": "gabc"}'
    sync_reminder_schedules(
        "gabc", {**rem, "second_nudge": True}, "Asia/Kolkata", client=fake, **arns
    )
    assert ("update", "daybook-reminder-gabc-first") in fake.calls
    assert fake.schedules["daybook-reminder-gabc-second"]["State"] == "ENABLED"
    sync_reminder_schedules("gabc", {**rem, "enabled": False}, "Asia/Kolkata", client=fake, **arns)
    assert {s["State"] for s in fake.schedules.values()} == {"DISABLED"}
    delete_reminder_schedules("gabc", client=fake)
    delete_reminder_schedules("gabc", client=fake)  # already gone: no error
    assert fake.schedules == {}


def test_settings_save_moves_schedules(tmp_path, monkeypatch):
    import hashlib

    from fastapi.testclient import TestClient

    from daybook.engine.app import set_app
    from daybook.main import api
    from daybook.push import schedules

    calls = []
    monkeypatch.setattr(
        schedules, "sync_reminder_schedules", lambda *a, **k: calls.append((a, k)) or []
    )
    app = make_app(
        tmp_path,
        scheduler_role_arn="arn:role",
        worker_function_arn="arn:worker",
        owner_key_hash=hashlib.sha256(b"k3y").hexdigest(),
    )
    set_app(app)
    try:
        h = {"X-Requested-With": "daybook"}
        with TestClient(api) as c:
            c.post("/api/demo/start", headers=h)
            r = c.put("/api/settings", headers=h, json={"reminder": {"enabled": True}})
            assert r.status_code == 200
            assert calls == []  # a demo never creates schedules
        with TestClient(api) as c:
            assert (
                c.post("/api/auth/login", headers=h, json={"owner_key": "k3y"}).status_code == 200
            )
            c.put("/api/settings", headers=h, json={"voice": False})
            assert calls == []  # nothing about reminders changed
            c.put("/api/settings", headers=h, json={"reminder": {"enabled": True, "time": "20:00"}})
            (uid, rem, tz), kw = calls[0]
            assert uid == "owner" and rem["time"] == "20:00" and rem["enabled"]
            assert tz == "Asia/Kolkata" and kw == {
                "worker_arn": "arn:worker",
                "role_arn": "arn:role",
            }
    finally:
        set_app(None)


async def test_today_ends_stale_session(tmp_path):
    from fastapi.testclient import TestClient

    from daybook.engine.app import set_app
    from daybook.main import api

    app = make_app(tmp_path)
    set_app(app)
    try:
        with TestClient(api) as c:
            h = {"X-Requested-With": "daybook", "X-Debug-Now": "2026-10-27T19:05:00+05:30"}
            c.post("/api/demo/start", headers=h)
            sid = c.post("/api/sessions", headers=h, json={"mode": "guided"}).json()["session"][
                "id"
            ]
            assert c.get("/api/today", headers=h).json()["active_session_id"] == sid
            later = {**h, "X-Debug-Now": "2026-10-27T19:40:00+05:30"}  # limit + 15 min passed
            assert c.get("/api/today", headers=later).json()["active_session_id"] is None
            assert (
                c.get(f"/api/sessions/{sid}", headers=later).json()["session"]["status"] != "active"
            )
    finally:
        set_app(None)


def test_ssm_secrets(monkeypatch):
    import sys
    import types

    from daybook import settings

    class SSM:
        def get_paginator(self, _):
            return self

        def paginate(self, Path, WithDecryption):
            assert Path == "/daybook" and WithDecryption
            return [
                {
                    "Parameters": [
                        {"Name": "/daybook/gemini_api_key", "Value": "g"},
                        {"Name": "/daybook/site_url", "Value": "https://x"},
                    ]
                }
            ]

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda _: SSM()))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("SITE_URL", "https://already-set")
    assert settings.load_ssm_secrets("/daybook") == 2
    import os

    assert os.environ["GEMINI_API_KEY"] == "g"
    assert os.environ["SITE_URL"] == "https://already-set"  # the environment wins
    monkeypatch.delenv("GEMINI_API_KEY")
