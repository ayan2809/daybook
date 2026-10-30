"""Auth, demo and /me (plan 11, 14.1, 15)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from daybook.api.deps import (
    COOKIE,
    ApiError,
    app_dep,
    check_owner_key,
    current_user,
    demo_rate_limit,
    login_rate_limit,
    now_dep,
    optional_user,
    read_cookie,
    set_cookie,
    signed_in,
    vault_rate_limit,
)
from daybook.api.settings_api import read_settings
from daybook.engine import accounts, demo
from daybook.engine.app import App, User

router = APIRouter()


class Login(BaseModel):
    owner_key: str


class GoogleLogin(BaseModel):
    credential: str  # the ID token from Google Identity Services


@router.post("/auth/login", dependencies=[Depends(login_rate_limit)])
def login(body: Login, response: Response, app: App = Depends(app_dep)):
    if not check_owner_key(app, body.owner_key):
        raise ApiError(401, "unauthorized", "That key didn't work.")
    user = accounts.owner_sign_in(app)
    set_cookie(response, app, "owner", user=user)
    return {"role": "owner", "vault": accounts.vault_state(app, user.aid)}


@router.post("/auth/google", dependencies=[Depends(login_rate_limit)])
def login_google(body: GoogleLogin, response: Response, app: App = Depends(app_dep)):
    """Sign in (and on first use, sign up) with Google (DB-29)."""
    if not app.settings.google_client_id:
        raise ApiError(503, "unavailable", "Google sign-in isn't set up.")
    try:
        claims = accounts.verify_google(body.credential, app.settings.google_client_id)
    except ValueError:
        raise ApiError(401, "unauthorized", "That Google sign-in didn't work. Try again.") from None
    try:
        user = accounts.sign_in(app, claims)
    except accounts.AccountError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    first = claims.get("given_name") or claims.get("name")
    set_cookie(response, app, user.role, user=user, name=first)
    return {"role": user.role, "vault": accounts.vault_state(app, user.aid)}


# ------------------------------------------------------------------ the sealed vault (DB-32)


class VaultKey(BaseModel):
    recovery_key: str


def _account(user: User) -> User:
    if not user.is_account:
        raise ApiError(403, "forbidden", "Sign in to use this.")
    return user


@router.post("/vault/setup", dependencies=[Depends(vault_rate_limit)])
def vault_setup(
    body: VaultKey,
    request: Request,
    response: Response,
    app: App = Depends(app_dep),
    user: User = Depends(signed_in),
):
    """First unlock of a new account: the browser made the recovery key and shows it once."""
    _account(user)
    name = (read_cookie(request, app) or {}).get("nm")
    try:
        opened = accounts.setup_vault(app, user, body.recovery_key, display_name=name)
    except ValueError as exc:
        raise ApiError(422, "validation", str(exc)) from None
    except accounts.AccountError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    set_cookie(response, app, user.role, user=opened)
    return {"vault": "open"}


@router.post("/vault/unlock", dependencies=[Depends(vault_rate_limit)])
def vault_unlock(
    body: VaultKey, response: Response, app: App = Depends(app_dep), user: User = Depends(signed_in)
):
    _account(user)
    try:
        opened = accounts.unlock(app, user, body.recovery_key)
    except accounts.AccountError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    set_cookie(response, app, user.role, user=opened)
    return {"vault": "open"}


class ResetBody(BaseModel):
    confirm: str


@router.post("/vault/reset", dependencies=[Depends(vault_rate_limit)])
def vault_reset(
    body: ResetBody,
    response: Response,
    app: App = Depends(app_dep),
    user: User = Depends(signed_in),
):
    """Lost key: start over with an empty vault. The old data stays sealed and unreachable."""
    _account(user)
    if body.confirm != "start over":
        raise ApiError(422, "validation", 'Type "start over" to confirm.')
    accounts.reset(app, user)
    locked = accounts.account_user(user.role, user.aid, None, user.email)
    set_cookie(response, app, user.role, user=locked)
    return {"vault": "new"}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True}


@router.get("/me")
def me(
    app: App = Depends(app_dep),
    user: User | None = Depends(optional_user),
    now: datetime = Depends(now_dep),
):
    features = {
        "voice": app.tts is not None,
        "push": bool(app.settings.vapid_public_key),
        "vapid_public_key": app.settings.vapid_public_key or None,
        "google_client_id": app.settings.google_client_id or None,
    }
    if user is None:
        return {"role": None, "settings": None, "demo": None, "features": features, "account": None}
    account = None
    if user.is_account:
        vault = "open" if not user.locked else accounts.vault_state(app, user.aid)
        account = {"email": user.email, "vault": vault}
    out = {
        "role": user.role,
        "settings": None if user.locked else read_settings(app, user),
        "demo": None,
        "features": features,
        "account": account,
    }
    if user.role == "demo":
        usage = app.demo_caps.usage(user.sandbox_id, now)
        replay = usage["sessions_left"] <= 0 or app.spend.status("demo", now) != "ok"
        out["demo"] = {"sandbox_id": user.sandbox_id, **usage, "replay_only": replay}
        out["features"]["push"] = False
    return out


@router.post("/demo/start", dependencies=[Depends(demo_rate_limit)])
async def demo_start(
    response: Response, app: App = Depends(app_dep), now: datetime = Depends(now_dep)
):
    if not app.demo_caps.new_sandbox(now):
        raise ApiError(429, "demo_cap", "The demo is full for today. Please try again tomorrow.")
    user = await demo.create_sandbox(app, now)
    set_cookie(response, app, "demo", user.sandbox_id)
    return {"role": "demo", "sandbox_id": user.sandbox_id}


@router.get("/demo/replay")
def demo_replay(user: User = Depends(current_user)):
    return demo.replay_payload()


@router.get("/demo/last-trace")
def demo_last_trace(app: App = Depends(app_dep), user: User = Depends(current_user)):
    if user.role != "demo":
        raise ApiError(403, "forbidden", "Demo only.")
    from daybook.engine import conductor
    from daybook.engine import state as st

    sid = conductor.active_pointer(app, user)
    keys = app.store.list(f"{user.prefix}sessions/")
    cands = [sid] if sid else []
    cands += [k.rsplit("/", 1)[1][:-5] for k in reversed(keys)]
    for s in cands:
        loaded = st.load(app.store_for(user), user.prefix, s)
        if loaded and loaded[0].flags.get("last_trace"):
            return loaded[0].flags["last_trace"]
    return None
