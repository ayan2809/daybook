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
