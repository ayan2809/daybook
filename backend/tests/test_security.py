"""DB-31: every API route is private unless it is on the public list; abuse limits on the rest."""

import re

from fastapi.testclient import TestClient

from daybook.api import deps
from daybook.engine.app import set_app
from daybook.main import PUBLIC, api
from tests.helpers import make_app

H = {"X-Requested-With": "daybook"}


def _routes():
    for path, ops in api.openapi()["paths"].items():
        for method in ops:
            yield method.upper(), path


def test_every_route_needs_a_session_unless_public(tmp_path):
    set_app(make_app(tmp_path))
    try:
        c = TestClient(api)
        forged = deps.URLSafeTimedSerializer("not-the-key", salt="daybook-session").dumps(
            {"role": "owner"}
        )
        checked = 0
        for method, path in _routes():
            if (method, path) in PUBLIC:
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            for cookie in (None, forged):
                c.cookies.clear()
                if cookie:
                    c.cookies.set(deps.COOKIE, cookie)
                r = c.request(method, url, headers=H, json={})
                assert r.status_code == 401, (method, path, cookie is not None, r.status_code)
            checked += 1
        assert checked >= 35  # the sweep really covered the API
    finally:
        set_app(None)


def test_public_list_is_small():
    assert {p for _, p in PUBLIC} <= {
        "/api/health",
        "/api/me",
        "/api/auth/login",
        "/api/auth/google",
        "/api/auth/logout",
        "/api/demo/start",
        "/api/docs",
        "/api/openapi.json",
    }


def test_demo_start_limits(tmp_path, monkeypatch):
    app = make_app(tmp_path, demo_daily_sandboxes=4)
    set_app(app)
    try:
        monkeypatch.setattr(deps, "_demo", deps.TokenBucket(rate_per_min=3, burst=3))
        c = TestClient(api)
        codes = [c.post("/api/demo/start", headers=H).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]  # per-IP burst of 3
        monkeypatch.setattr(deps, "_demo", deps.TokenBucket(rate_per_min=600, burst=600))
        assert c.post("/api/demo/start", headers=H).status_code == 200  # 4th sandbox today
        r = c.post("/api/demo/start", headers=H)  # past the daily cap of 4
        assert r.status_code == 429 and r.json()["error"]["code"] == "demo_cap"
    finally:
        set_app(None)


def test_client_ip_trusts_the_cloudfront_header_only_behind_cloudfront(tmp_path):
    class Req:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "10.0.0.1"})()

    set_app(make_app(tmp_path, origin_verify=""))
    try:
        assert deps.client_ip(Req({"x-viewer-ip": "1.2.3.4"})) == "10.0.0.1"  # local: ignore it
    finally:
        set_app(None)
    set_app(make_app(tmp_path, origin_verify="secret"))
    try:
        assert deps.client_ip(Req({"x-viewer-ip": "1.2.3.4"})) == "1.2.3.4"
    finally:
        set_app(None)


def test_anonymous_warm_does_no_work(tmp_path):
    app = make_app(tmp_path)
    set_app(app)
    try:
        c = TestClient(api)
        assert c.get("/api/health?warm=1").status_code == 200
        assert app.store.list("users/") == []  # no vault was opened for anyone
    finally:
        set_app(None)
