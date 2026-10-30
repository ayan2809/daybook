"""DB-32: sealed vaults. Nothing an account says is readable in storage, and nothing in storage
names the person or links their sign-in to their folder."""

import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from daybook.engine import accounts
from daybook.engine.app import OWNER, set_app
from daybook.main import api
from daybook.memory import repo
from daybook.storage import sealed
from daybook.worker import _user, job_identity
from tests.helpers import RK, RK2, XRW, make_app, open_vault

SECRET = "Zanzibar migration cut checkout latency from 900 ms to 300 ms"
NOW = {"X-Debug-Now": "2026-10-27T19:05:00+05:30"}


@pytest.fixture
def setup(tmp_path):
    app = make_app(tmp_path, owner_key_hash=hashlib.sha256(b"k3y").hexdigest())
    set_app(app)
    yield app, lambda: TestClient(api)
    set_app(None)


def login(c):
    assert c.post("/api/auth/login", headers=XRW, json={"owner_key": "k3y"}).status_code == 200


def reflect(c, text: str) -> str:
    sid = c.post("/api/sessions", headers=XRW, json={"mode": "quick"}).json()["session"]["id"]
    r = c.post(
        f"/api/sessions/{sid}/turns",
        headers=XRW,
        json={"client_turn_id": uuid.uuid4().hex, "text": text, "dictated_chars": len(text)},
    )
    assert r.status_code == 200, r.text
    c.post(f"/api/sessions/{sid}/end", headers=XRW, json={"reason": "user"})
    for _ in range(50):
        if c.get(f"/api/sessions/{sid}/summary").json()["status"] == "finalized":
            break
    return sid


def all_blobs(app) -> dict[str, bytes]:
    return {k: app.store.get(k).data for k in app.store.list("")}


def test_nothing_readable_at_rest(setup):
    app, client = setup
    with client() as c:
        login(c)
        open_vault(c, RK)
        c.put("/api/settings", headers=XRW, json={"profile": {"display_name": "Ayan S"}})
        reflect(c, SECRET)
        assert any("Zanzibar" in i["what"] for i in c.get("/api/memories").json()["items"])
        c.get(f"/api/turns/{reflect(c, 'Short day.')}.2/audio")
    blobs = all_blobs(app)
    user_keys = [k for k in blobs if k.startswith("users/")]
    assert user_keys and all(k.startswith("users/v") for k in user_keys)
    for k, data in blobs.items():
        assert b"Zanzibar" not in data and b"Ayan S" not in data, k
        if k.startswith("users/"):
            assert sealed.is_sealed(data), f"{k} is not sealed"
    # the account record names nobody and doesn't point at the folder
    rec = json.loads(blobs["accounts/owner.json"])
    assert set(rec) == {"created", "check"}
    folder = user_keys[0].split("/")[1]
    assert folder not in json.dumps(rec)


def test_wrong_key_new_device_and_reset(setup):
    app, client = setup
    with client() as c:
        login(c)
        open_vault(c, RK)
        c.put("/api/settings", headers=XRW, json={"profile": {"display_name": "Ayan S"}})
    with client() as d:  # a new device: sign in, then the recovery key
        login(d)
        assert d.get("/api/me").json()["account"]["vault"] == "locked"
        r = d.post("/api/vault/unlock", headers=XRW, json={"recovery_key": RK2})
        assert r.status_code == 403 and r.json()["error"]["code"] == "wrong_key"
        r = d.post("/api/vault/unlock", headers=XRW, json={"recovery_key": "not a key"})
        assert r.status_code == 403
        assert (
            d.post("/api/vault/setup", headers=XRW, json={"recovery_key": RK2}).status_code == 409
        )
        open_vault(d, RK.lower().replace("-", " "))  # case and separators don't matter
        assert d.get("/api/today").json()["display_name"] == "Ayan S"
        # lost key: start over; the old folder stays sealed and out of reach
        d.post("/api/vault/reset", headers=XRW, json={"confirm": "start over"})
        assert d.get("/api/today").status_code == 423
        open_vault(d, RK2)
        assert d.get("/api/today").json()["display_name"] is None
        r = d.post("/api/vault/unlock", headers=XRW, json={"recovery_key": RK})
        assert r.status_code == 409 or r.json()["error"]["code"] == "wrong_key"


def test_cookie_carries_the_key_encrypted(setup):
    app, client = setup
    with client() as c:
        login(c)
        open_vault(c, RK)
        raw = c.cookies.get("db_session")
    vk = sealed.vault_key(sealed.parse_recovery_key(RK))
    payload = raw.split(".")[0]
    import base64

    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert vk.hex().encode() not in decoded and base64.urlsafe_b64encode(vk) not in decoded


def test_sealed_blob_is_bound_to_its_path(setup):
    app, _ = setup
    vk = sealed.vault_key(sealed.parse_recovery_key(RK))
    s = sealed.sealed_for(app.store, vk)
    s.put("users/vx/a.json", b"hello")
    app.store.put("users/vx/b.json", app.store.get("users/vx/a.json").data)  # moved by hand
    with pytest.raises(sealed.SealError):
        s.get("users/vx/b.json")
    other = sealed.sealed_for(app.store, sealed.vault_key(sealed.parse_recovery_key(RK2)))
    with pytest.raises(sealed.SealError):
        other.get("users/vx/a.json")
    assert s.get("users/vx/a.json").data == b"hello"


def test_worker_event_carries_the_key_encrypted(setup):
    app, _ = setup
    vk = sealed.vault_key(sealed.parse_recovery_key(RK))
    user = accounts.account_user("owner", "owner", vk)
    ev = job_identity(app, user)
    assert vk.hex() not in json.dumps(ev) and "uid" not in ev
    back = _user(app, {"job": "finalize_session", **ev})
    assert back.key == vk and back.prefix == user.prefix and back.role == "owner"


def test_owner_plain_data_is_sealed_on_first_unlock(setup):
    app, client = setup
    app.vault(OWNER).write(
        lambda c: repo.upsert_profile(c, display_name="Old Ayan", timezone="Asia/Kolkata")
    )
    app.store.put("traces/owner/2026-10-26/x/001.json", b'{"spans": []}')
    assert app.store.list("users/owner/")
    with client() as c:
        login(c)
        open_vault(c, RK)
        assert c.get("/api/today").json()["display_name"] == "Old Ayan"
    assert app.store.list("users/owner/") == [] and app.store.list("traces/owner/") == []
    assert all(sealed.is_sealed(d) for k, d in all_blobs(app).items() if k.startswith("users/"))


def test_brain_export(setup):
    app, client = setup
    with client() as c:
        login(c)
        open_vault(c, RK)
        c.put("/api/settings", headers=XRW, json={"profile": {"display_name": "Ayan"}})
        reflect(c, SECRET)
        r = c.get("/api/export/brain")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert md.startswith("# Ayan's work memory (Daybook export)")
    assert "For the AI reading this" in md and "Zanzibar" in md and "## Profile" in md
