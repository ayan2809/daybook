"""HTTP flows with the fake LLM: A13–A15 and the demo path (L1–L4)."""

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from daybook.engine.app import set_app
from daybook.main import api
from tests.helpers import make_app

H = {"X-Requested-With": "daybook"}
NOW = {"X-Debug-Now": "2026-10-27T19:05:00+05:30"}  # a Tuesday


@pytest.fixture
def client(tmp_path):
    app = make_app(tmp_path, owner_key_hash=hashlib.sha256(b"k3y").hexdigest(), origin_verify="")
    set_app(app)
    with TestClient(api) as c:
        c.app_ = app
        yield c
    set_app(None)


def demo(client):
    r = client.post("/api/demo/start", headers={**H, **NOW})
    assert r.status_code == 200, r.text
    return r.json()["sandbox_id"]


def turn(client, sid, text, **hdr):
    r = client.post(
        f"/api/sessions/{sid}/turns",
        headers={**H, **NOW, **hdr},
        json={"client_turn_id": uuid.uuid4().hex, "text": text, "dictated_chars": len(text)},
    )
    assert r.status_code == 200, r.text
    return r.json()


# A14
def test_auth_rules(client):
    assert client.get("/api/today").status_code == 401
    assert client.get("/api/me").json()["role"] is None
    assert client.post("/api/demo/start").status_code == 403  # no X-Requested-With
    assert client.post("/api/auth/login", headers=H, json={"owner_key": "nope"}).status_code == 401
    demo(client)
    r = client.get("/api/admin/sessions")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (
        client.post("/api/auth/login", headers=H, json={"owner_key": "k3y"}).json()["role"]
        == "owner"
    )
    assert client.get("/api/admin/sessions").status_code == 200


def test_origin_lock(client):
    client.app_.settings.origin_verify = "s3cret"
    assert client.get("/api/health").status_code == 403
    assert client.get("/api/health", headers={"X-Origin-Verify": "s3cret"}).status_code == 200
    client.app_.settings.origin_verify = ""


# L1–L4 on the seeded demo
def test_demo_today_and_reflection(client):
    demo(client)
    t = client.get("/api/today", headers=NOW).json()
    assert len(t["pending"]) == 2 and t["streak"][-2]["reflected"]  # yesterday reflected
    r = client.post("/api/sessions", headers={**H, **NOW}, json={"mode": "guided"})
    assert r.status_code == 201, r.text
    s = r.json()["session"]
    assert s["phase"] == "close_loops" and "last time" in s["turns"][-1]["text"]
    assert s["limit_sec"] == 180 and s["suggestions"]
    sid = s["id"]
    r = turn(client, sid, "It went from about 40 alerts a week to around 6.")
    assert r["pending_action"]["kind"] == "confirm_metric", r
    r = turn(client, sid, "Yes.")
    assert r["pending_action"] is None
    audio = client.get(f"/api/turns/{r['turn_id']}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    r = turn(client, sid, "Not sure yet, I'll check tomorrow.")  # the second pending
    r = turn(
        client,
        sid,
        "I finished the load test: search held up at 450 searches a second.",
    )
    r = turn(client, sid, "stop")
    assert r["ended"]
    # finalize runs in-process; poll the summary
    for _ in range(50):
        sm = client.get(f"/api/sessions/{sid}/summary").json()
        if sm["status"] == "finalized":
            break
    assert sm["status"] == "finalized", sm
    assert any("load test" in i["title"].lower() for i in sm["items"])
    t = client.get("/api/today", headers=NOW).json()
    assert t["reflected_today"]


def test_fix_by_voice_on_seeded_item(client):
    demo(client)
    items = client.get("/api/memories", params={"q": "50%"}).json()["items"]
    target = next(i for i in items if any(m["delta_pct"] == -50 for m in i["metrics"]))
    r = client.post(
        "/api/sessions", headers={**H, **NOW}, json={"mode": "edit", "item_id": target["id"]}
    )
    s = r.json()["session"]
    assert s["pending_action"]["kind"] == "describe_edit"
    r = turn(client, s["id"], "It was 40% faster, not 50%.")
    assert (
        r["pending_action"]["kind"] == "confirm_edit"
        and "50%" in r["reply_text"]
        and "40%" in r["reply_text"]
    )
    r = turn(client, s["id"], "Yes.")
    assert r["ended"] and "Done, updated" in r["reply_text"]
    d = client.get(f"/api/memories/{target['id']}").json()
    assert d["item"]["metrics"][0]["delta_pct"] == -40
    by = {v["version"]: v["changed_by"] for v in d["versions"]}
    assert by[1] == "analyzer" and by[2] == "user_voice"  # v3+: finalize may refresh the bullet


def test_fix_by_voice_inside_guided_session(client):
    demo(client)
    sid = client.post("/api/sessions", headers={**H, **NOW}, json={"mode": "guided"}).json()[
        "session"
    ]["id"]
    turn(client, sid, "skip")
    turn(client, sid, "skip")
    r = turn(client, sid, "Actually, the article import last Tuesday was 40% faster, not 50.")
    assert r["pending_action"]["kind"] in ("confirm_item", "pick_item"), r
    if r["pending_action"]["kind"] == "pick_item":
        r = turn(client, sid, "the import one")
    else:
        r = turn(client, sid, "Yes, that one.")
    assert r["pending_action"]["kind"] == "confirm_edit", r


def test_ask_and_abstain(client):
    demo(client)
    r = client.post(
        "/api/ask", headers={**H, **NOW}, json={"question": "When did I present to the CFO?"}
    )
    assert r.status_code == 200 and r.json()["abstained"]
    r = client.post(
        "/api/ask",
        headers={**H, **NOW},
        json={"question": "What did I do on the trip assistant?"},
    ).json()
    assert not r["abstained"] and r["citations"]
    assert all(str(c["item_id"]) in r["refs"] for c in r["citations"])


def test_typed_pending_answer_and_timeline_edit(client):
    demo(client)
    p = client.get("/api/pending", headers=NOW).json()["pending"]
    metric_q = next(q for q in p if q["kind"] == "metric")
    c = client.post(
        f"/api/pending/{metric_q['id']}/answer",
        headers={**H, **NOW},
        json={"text": "from 40 to 6 alerts a week"},
    ).json()["candidate"]
    assert c["kind"] == "metric"
    r = client.post(
        f"/api/pending/{metric_q['id']}/confirm", headers={**H, **NOW}, json={"candidate": c}
    )
    assert r.json()["pending"]["status"] == "answered"
    assert r.json()["item"]["metrics"][0]["baseline"] == 40
    iid = r.json()["item"]["id"]
    r = client.patch(
        f"/api/memories/{iid}", headers=H, json={"fields": {"title": "Tuned search alerts"}}
    )
    assert r.json()["item"]["title"] == "Tuned search alerts"
    v1 = client.get(f"/api/memories/{iid}").json()["versions"][-1]["version"]
    r = client.post(f"/api/memories/{iid}/restore", headers=H, json={"version": v1})
    assert r.json()["item"]["title"] != "Tuned search alerts"
    assert client.post(f"/api/memories/{iid}/hide", headers=H).json()["item"]["status"] == "hidden"


def test_demo_caps_replay(client):
    client.app_.settings.demo_daily_sessions = 0
    demo(client)
    assert client.get("/api/me", headers=NOW).json()["demo"]["replay_only"]
    r = client.post("/api/sessions", headers={**H, **NOW}, json={"mode": "guided"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "demo_cap"


def test_settings_roundtrip_and_validation(client):
    client.post("/api/auth/login", headers=H, json={"owner_key": "k3y"})
    s = client.put(
        "/api/settings",
        headers=H,
        json={
            "session_limit_sec": 300,
            "reminder": {"time": "18:30"},
            "profile": {"org": "Side projects"},
        },
    ).json()
    assert s["session_limit_sec"] == 300 and s["reminder"]["time"] == "18:30"
    assert s["reminder"]["second_time"] == "21:00" and s["profile"]["org"] == "Side projects"
    r = client.put("/api/settings", headers=H, json={"session_limit_sec": 999})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
