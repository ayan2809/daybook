"""Request plumbing: errors, auth cookie, CSRF header, origin lock, time travel, rate limits
(plan 11, 14.1).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from daybook.engine.app import App, User, account_user, demo_user, get_app

COOKIE = "db_session"
OWNER_TTL = 180 * 24 * 3600
DEMO_TTL = 24 * 3600


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra


def error_response(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def app_dep() -> App:
    return get_app()


# ------------------------------------------------------------------ cookies


def _serializer(app: App) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.settings.session_signing_key, salt="daybook-session")


def make_cookie(
    app: App, role: str, sandbox_id: str | None = None, user: User | None = None
) -> str:
    data = {"role": role, "sb": sandbox_id}
    if user is not None:
        data.update(uid=user.uid, em=user.email)
    return _serializer(app).dumps(data)


def set_cookie(
    response, app: App, role: str, sandbox_id: str | None = None, user: User | None = None
) -> None:
    response.set_cookie(
        COOKIE,
        make_cookie(app, role, sandbox_id, user),
        max_age=DEMO_TTL if role == "demo" else OWNER_TTL,
        httponly=True,
        secure=not app.settings.is_local,
        samesite="strict",
        path="/api",
    )


def read_cookie(request: Request, app: App) -> dict | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _serializer(app).loads(raw, max_age=OWNER_TTL)
    except BadSignature:
        return None
    if data.get("role") == "demo":
        try:
            _serializer(app).loads(raw, max_age=DEMO_TTL)
        except BadSignature:
            return None
    return data


def optional_user(request: Request, app: App = Depends(app_dep)) -> User | None:
    data = read_cookie(request, app)
    if not data:
        return None
    if data["role"] == "owner":
        return account_user("owner", data.get("em"))
    if data["role"] == "user" and data.get("uid"):
        return account_user(data["uid"], data.get("em"))
    if data["role"] == "demo" and data.get("sb"):
        return demo_user(data["sb"])
    return None


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ApiError(401, "unauthorized", "Please sign in or start the demo.")
    return user


def owner_only(user: User = Depends(current_user)) -> User:
    """Admin pages: Ayan only."""
    if user.role != "owner":
        raise ApiError(403, "forbidden", "Owner only.")
    return user


def account_only(user: User = Depends(current_user)) -> User:
    """A signed-in account (not the demo): push, exports, delete."""
    if not user.is_account:
        raise ApiError(403, "forbidden", "Sign in to use this.")
    return user


def check_owner_key(app: App, key: str) -> bool:
    want = app.settings.owner_key_hash
    if not want or not key:
        return False
    got = hashlib.sha256(key.strip().encode()).hexdigest()
    return hmac.compare_digest(got, want.strip().lower())


# ------------------------------------------------------------------ time travel


def now_dep(
    request: Request, app: App = Depends(app_dep), user: User | None = Depends(optional_user)
) -> datetime:
    """`X-Debug-Now` works only locally and in demo sandboxes (plan 24)."""
    hdr = request.headers.get("x-debug-now")
    if hdr and (app.settings.is_local or (user and user.role == "demo")):
        try:
            dt = datetime.fromisoformat(hdr)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            raise ApiError(422, "validation", "Bad X-Debug-Now header.") from None
    return datetime.now(UTC)


# ------------------------------------------------------------------ rate limits


class TokenBucket:
    """Per-key token bucket, in memory per instance (plan 14.1)."""

    def __init__(self, rate_per_min: float, burst: int):
        self.rate = rate_per_min / 60.0
        self.burst = burst
        self.state: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self.state.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.state[key] = (tokens, now)
            return False
        self.state[key] = (tokens - 1, now)
        if len(self.state) > 10_000:
            self.state.clear()
        return True


_general = TokenBucket(rate_per_min=120, burst=40)
_login = TokenBucket(rate_per_min=5, burst=5)
_demo = TokenBucket(rate_per_min=3, burst=3)


def client_ip(request: Request) -> str:
    """The viewer's IP. Behind CloudFront the Lambda only sees CloudFront's address, so a
    CloudFront Function writes the viewer IP into X-Viewer-IP (overwriting anything the client
    sent). It is trusted only when the request also proved it came through CloudFront."""
    app = get_app()
    if app.settings.origin_verify:
        return request.headers.get("x-viewer-ip") or "unknown"
    return request.client.host if request.client else "anon"


def rate_limit(request: Request) -> None:
    key = request.cookies.get(COOKIE) or client_ip(request)
    if not _general.take(key):
        raise ApiError(429, "rate_limited", "Too many requests. Slow down a little.")


def login_rate_limit(request: Request) -> None:
    if not _login.take(client_ip(request)):
        raise ApiError(429, "rate_limited", "Too many sign-in attempts. Try again in a minute.")


def demo_rate_limit(request: Request) -> None:
    if not _demo.take(client_ip(request)):
        raise ApiError(429, "rate_limited", "Too many demos started. Try again in a minute.")
