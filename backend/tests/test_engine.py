"""Engine flows in fake mode: A5–A7, A9, A11 and the guided path end to end."""

import uuid

import pytest

from daybook.engine import conductor
from daybook.engine.app import OWNER
from daybook.engine.conductor import SessionError
from daybook.engine.safety import TEST_PHRASES
from daybook.memory import repo
from tests.helpers import at, make_app


async def turn(app, sid, text, t):
    return await conductor.handle_turn(app, OWNER, sid, uuid.uuid4().hex, text, len(text), at(t))


async def test_guided_session_end_to_end(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    assert s.phase == "explore" and s.turns[0].role == "assistant"
    r = await turn(app, s.id, "I finally shipped the webhook retry worker today.", 20)
    assert r["board"]["items"][0]["chip"] == "needs_number"
    assert "?" in r["reply_text"]
    r = await turn(app, s.id, "From about 1,200 failed deliveries a day to under 100.", 50)
    assert r["board"]["items"][0]["chip"] == "number_captured"
    r = await turn(app, s.id, "Spent an hour helping the new intern debug her first PR.", 80)
    chips = {i["chip"] for i in r["board"]["items"]}
    assert "soft_skill" in chips
    r = await turn(app, s.id, "Let's wrap up", 110)
    assert r["phase"] == "wrap" and "miss" in r["reply_text"].lower()
    r = await turn(app, s.id, "No, that's it.", 130)
    assert r["ended"]
    await app.drain()
    with app.vault(OWNER).read() as conn:
        items = repo.list_items(conn)
        sess = repo.get_session_row(conn, s.id)
        pend = repo.open_pending(conn)
    assert sess["status"] == "finalized" and sess["stats"]["judge"]["goal_adherence"] == 1.0
    retry = next(i for i in items if "retry" in i["title"].lower())
    assert retry["metrics"][0]["baseline"] == 1200 and retry["metrics"][0]["final"] == 100
    assert retry["metrics"][0]["delta_pct"] == pytest.approx(-91.67)
    soft = next(i for i in items if i["kind"] == "soft_skill")
    assert soft["metrics"] == []
    # the soft skill's outcome question was promised in the wrap and must exist
    assert any(p["item_id"] == soft["id"] for p in pend)


async def test_idempotent_turn_and_time_up(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    cid = "same-id"
    r1 = await conductor.handle_turn(app, OWNER, s.id, cid, "I fixed a flaky test.", 5, at(10))
    r2 = await conductor.handle_turn(app, OWNER, s.id, cid, "I fixed a flaky test.", 5, at(12))
    assert r1 == r2
    with pytest.raises(SessionError) as e:
        await turn(app, s.id, "more", 420 + 20)
    assert e.value.code == "time_up"
    await app.drain()


async def test_session_active_conflict(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    with pytest.raises(SessionError) as e:
        await conductor.start_session(app, OWNER, "guided", at(5))
    assert e.value.code == "session_active" and e.value.extra["session_id"] == s.id


async def test_wrap_starts_on_time_and_stop_ends(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    r = await turn(app, s.id, "I reviewed three pull requests.", 370)  # 6:10 > 7:00 - 60s
    assert r["phase"] == "wrap"
    s2 = await conductor.start_session(app, OWNER, "guided", at(1000))  # first expired → ended
    r = await turn(app, s2.id, "stop", 1010)
    assert r["ended"] and "stopping" in r["reply_text"].lower()
    await app.drain()


async def test_safety_path_skips_llm(tmp_path):
    app = make_app(tmp_path)
    s = await conductor.start_session(app, OWNER, "guided", at(0))
    n_calls = len(app.llm.calls)
    r = await turn(app, s.id, TEST_PHRASES[0], 10)
    assert r["safety"]["helpline_number"] == "14416"
    assert len(app.llm.calls) == n_calls  # no LLM call
    loaded = conductor.st.load(app.store, OWNER.prefix, s.id)[0]
    assert loaded.turns[-2].text == "(not saved)"
