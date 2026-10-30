"""DB-29, DB-32: Google sign-in (sign-in is sign-up), one sealed vault per account, cost caps."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from daybook.engine import accounts
from daybook.engine.app import set_app
from daybook.main import api
from tests.helpers import RK, RK2, make_app, open_vault

H = {"X-Requested-With": "daybook"}


def claims(sub: str, email: str, **extra) -> dict:
    return {
        "iss": "https://accounts.google.com",
        "sub": sub,
        "email": email,
        "email_verified": True,
        "given_name": email.split("@")[0].title(),
        **extra,
    }


@pytest.fixture
def make(tmp_path, monkeypatch):
    """A client factory; the Google token is a fake: 'tok:<sub>:<email>'."""

    def verify(credential, client_id):
        assert client_id == "cid.apps.googleusercontent.com"
        if not credential.startswith("tok:"):
            raise ValueError("bad token")
        _, sub, email = credential.split(":", 2)
        return claims(sub, email)

    monkeypatch.setattr(accounts, "verify_google", verify)
    app = make_app(
        tmp_path,
        google_client_id="cid.apps.googleusercontent.com",
        owner_emails="ayan@example.com",
        owner_key_hash=hashlib.sha256(b"k3y").hexdigest(),
        max_accounts=2,
    )
    set_app(app)
    yield lambda: TestClient(api)
    set_app(None)


def google(c, sub, email, key=None):
    r = c.post("/api/auth/google", headers=H, json={"credential": f"tok:{sub}:{email}"})
    if key and r.status_code == 200:
        open_vault(c, key)
    return r


def test_signup_gives_each_account_its_own_data(make):
    a, b = make(), make()
    with a, b:
        assert a.get("/api/me").json()["features"]["google_client_id"].startswith("cid.")
        r = google(a, "111", "Asha@Example.com")
        assert r.status_code == 200 and r.json() == {"role": "user", "vault": "new"}
        me = a.get("/api/me").json()
        assert me["account"] == {"email": "asha@example.com", "vault": "new"}
        assert me["settings"] is None and a.get("/api/today").status_code == 423
        open_vault(a, RK)
        assert a.get("/api/me").json()["account"]["vault"] == "open"
        assert a.get("/api/today").json()["display_name"] == "Asha"  # from Google, at sign-up
        a.put("/api/settings", headers=H, json={"profile": {"display_name": "Asha K"}})
        r = a.post("/api/sessions", headers=H, json={"mode": "quick"})
        assert r.status_code == 201
        assert google(b, "222", "bo@example.com", RK2).json()["role"] == "user"
        assert b.get("/api/today").json()["display_name"] == "Bo"
        assert b.get("/api/today").json()["active_session_id"] is None  # A's session isn't B's
        assert a.get("/api/today").json()["display_name"] == "Asha K"
        # signing in again opens the same account, not a new one, once the key is given
        assert google(a, "111", "asha@example.com").json()["vault"] == "locked"
        assert a.get("/api/today").status_code == 423
        open_vault(a, RK)
        assert a.get("/api/today").json()["display_name"] == "Asha K"
        assert len(accounts.all_aids(_app())) == 2


def _app():
    from daybook.engine.app import get_app

    return get_app()


def test_owner_email_opens_the_owner_vault(make):
    with make() as c:
        assert google(c, "999", "ayan@example.com", RK).json()["role"] == "owner"
        assert c.get("/api/admin/sessions").status_code == 200
        c.put("/api/settings", headers=H, json={"profile": {"display_name": "Ayan"}})
        assert accounts.all_aids(_app()) == ["owner"]
    with make() as c:  # the owner key opens the same vault, with the same recovery key
        r = c.post("/api/auth/login", headers=H, json={"owner_key": "k3y"})
        assert r.json() == {"role": "owner", "vault": "locked"}
        open_vault(c, RK)
        assert c.get("/api/me").json()["role"] == "owner"
        assert c.get("/api/today").json()["display_name"] == "Ayan"


def test_users_cannot_reach_admin_and_demo_cannot_export(make):
    with make() as c:
        google(c, "111", "asha@example.com", RK)
        assert c.get("/api/admin/sessions").status_code == 403
        assert c.get("/api/export/markdown").status_code == 200  # their own data
    with make() as c:
        c.post("/api/demo/start", headers=H)
        assert c.get("/api/export/markdown").status_code == 403
        assert c.get("/api/export/brain").status_code == 200  # made-up data: fine to take


def test_signup_limits(make, monkeypatch):
    with make() as c:
        assert google(c, "1", "a@example.com").status_code == 200
        assert google(c, "2", "b@example.com").status_code == 200
        r = google(c, "3", "c@example.com")  # max_accounts = 2
        assert r.status_code == 403 and r.json()["error"]["code"] == "signups_closed"
        assert google(c, "1", "a@example.com").status_code == 200  # existing accounts still sign in
        r = c.post("/api/auth/google", headers=H, json={"credential": "forged"})
        assert r.status_code == 401
    from daybook.api import deps

    deps._login.state.clear()  # six sign-ins above: past the per-minute burst
    _app().settings.allowed_emails = "friend@example.com"
    with make() as c:
        r = google(c, "4", "stranger@example.com")
        assert r.status_code == 403 and r.json()["error"]["code"] == "not_invited"


def test_unverified_email_is_refused(make):
    app = _app()
    with pytest.raises(accounts.AccountError):
        accounts.sign_in(app, claims("5", "x@example.com", email_verified=False))
    with pytest.raises(accounts.AccountError):
        accounts.sign_in(app, claims("5", "x@example.com", iss="https://evil.example"))


def test_user_budget_caps(make):
    app = _app()
    with make() as c:
        google(c, "111", "asha@example.com", RK)
        user = _opened("111", RK)
        app.spend.add(user, app.settings.user_daily_budget_usd, accounts.datetime.now(accounts.UTC))
        r = c.post("/api/sessions", headers=H, json={"mode": "guided"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "budget"
        assert (
            app.spend.status("owner", accounts.datetime.now(accounts.UTC)) == "ok"
        )  # owner unaffected


def test_delete_account(make):
    app = _app()
    with make() as c:
        google(c, "111", "asha@example.com", RK)
        user = _opened("111", RK)
        assert accounts.get(app, user.aid) and app.store.list(user.prefix)
        r = c.post("/api/data/delete", headers=H, json={"confirm": "delete"})
        assert r.status_code == 200
        assert accounts.get(app, user.aid) is None
        assert app.store.list(user.prefix) == []
        assert c.get("/api/me").json()["role"] is None  # signed out


def _opened(sub: str, rk: str):
    from daybook.storage import sealed

    app = _app()
    aid = sealed.account_id(app.settings.session_signing_key, sub)
    vk = sealed.vault_key(sealed.parse_recovery_key(rk))
    return accounts.account_user("user", aid, vk)
