"""Auth, demo and /me (plan 11, 14.1, 15)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from daybook.api.deps import (
    COOKIE,
    ApiError,
    app_dep,
    check_owner_key,
    current_user,
    login_rate_limit,
    now_dep,
    optional_user,
    set_cookie,
)
from daybook.api.settings_api import read_settings
from daybook.engine import demo
from daybook.engine.app import App, User

router = APIRouter()


class Login(BaseModel):
    owner_key: str


@router.post("/auth/login", dependencies=[Depends(login_rate_limit)])
def login(body: Login, response: Response, app: App = Depends(app_dep)):
    if not check_owner_key(app, body.owner_key):
        raise ApiError(401, "unauthorized", "That key didn't work.")
    set_cookie(response, app, "owner")
    return {"role": "owner"}


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
    }
    if user is None:
        return {"role": None, "settings": None, "demo": None, "features": features}
    out = {
        "role": user.role,
        "settings": read_settings(app, user),
        "demo": None,
        "features": features,
    }
    if user.role == "demo":
        usage = app.demo_caps.usage(user.sandbox_id, now)
        replay = usage["sessions_left"] <= 0 or app.spend.status("demo", now) != "ok"
        out["demo"] = {"sandbox_id": user.sandbox_id, **usage, "replay_only": replay}
        out["features"]["push"] = False
    return out


@router.post("/demo/start")
async def demo_start(
    response: Response, app: App = Depends(app_dep), now: datetime = Depends(now_dep)
):
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
        loaded = st.load(app.store, user.prefix, s)
        if loaded and loaded[0].flags.get("last_trace"):
            return loaded[0].flags["last_trace"]
    return None
