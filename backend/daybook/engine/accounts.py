"""Accounts (DB-29, DB-32): Google sign-in, where the first sign-in is the sign-up, and sealed vaults.

Nothing in storage names a person:
- `accounts/<aid>.json`: aid = HMAC(server key, Google's opaque `sub`) or "owner" (OWNER_EMAILS or
  the owner key). It holds only the day it was created and a key check value. No email, no name.
- `users/<folder>/…`: folder = HMAC(vault key, …), so the account record and the data folder can't
  be linked without the user's recovery key, and every object in the folder is sealed with it.

The recovery key is made in the browser and shown once. Daybook never stores it; the vault key
derived from it travels only inside the HttpOnly session cookie and, for a background job, inside
that job's (encrypted) event. Lose the key and the data can't be opened by anyone.
"""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime

from daybook.engine.app import OWNER, App, User, account_user
from daybook.memory import repo
from daybook.storage import sealed

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LEGACY_OWNER = (OWNER.prefix, f"traces/{OWNER.uid}/")  # plain data from before DB-32 (local dev)


class AccountError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _emails(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _key(aid: str) -> str:
    return f"accounts/{aid}.json"


def get(app: App, aid: str) -> dict | None:
    b = app.store.get(_key(aid))
    return json.loads(b.data) if b else None


def _put(app: App, aid: str, record: dict) -> None:
    app.store.put(_key(aid), json.dumps(record).encode(), content_type="application/json")


def all_aids(app: App) -> list[str]:
    return [k.split("/", 1)[1][:-5] for k in app.store.list("accounts/") if k.endswith(".json")]


def vault_state(app: App, aid: str) -> str:
    """new (no key yet) | locked (has a key)."""
    rec = get(app, aid)
    return "locked" if rec and rec.get("check") else "new"


def verify_google(credential: str, client_id: str) -> dict:
    """Check a Google ID token: signature (Google's keys), audience, expiry and issuer."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, g_requests.Request(), client_id)


def _ensure(app: App, aid: str, now: datetime | None) -> None:
    if get(app, aid) is None:
        _put(app, aid, {"created": (now or datetime.now(UTC)).date().isoformat()})


def sign_in(app: App, claims: dict, now: datetime | None = None) -> User:
    """Map verified Google claims to a (locked) account, creating it on first sign-in. The email is
    checked against the owner list and the invite list here, in memory, and never written down."""
    s = app.settings
    email = str(claims.get("email") or "").lower()
    if claims.get("iss") not in GOOGLE_ISSUERS or not claims.get("sub"):
        raise AccountError(401, "unauthorized", "That sign-in didn't come from Google.")
    if not email or not claims.get("email_verified"):
        raise AccountError(401, "unauthorized", "Your Google email isn't verified.")
    if email in _emails(s.owner_emails):
        _ensure(app, OWNER.uid, now)
        return account_user("owner", OWNER.uid, None, email)
    allowed = _emails(s.allowed_emails)
    if allowed and email not in allowed:
        raise AccountError(403, "not_invited", "This Daybook is invite-only for now.")
    aid = sealed.account_id(s.session_signing_key, str(claims["sub"]))
    if get(app, aid) is None:
        if len(all_aids(app)) >= s.max_accounts:
            raise AccountError(403, "signups_closed", "Sign-ups are full right now.")
        _ensure(app, aid, now)
    return account_user("user", aid, None, email)


def owner_sign_in(app: App, now: datetime | None = None) -> User:
    _ensure(app, OWNER.uid, now)
    return account_user("owner", OWNER.uid, None)


def setup_vault(app: App, user: User, recovery_key: str, display_name: str | None = None) -> User:
    """First unlock: remember the key check, then open (and, for old plain data, seal) the vault."""
    rec = get(app, user.aid) or {}
    if rec.get("check"):
        raise AccountError(409, "conflict", "This account already has a recovery key.")
    vk = sealed.vault_key(sealed.parse_recovery_key(recovery_key))
    rec["check"] = sealed.key_check(vk)
    opened = account_user(user.role, user.aid, vk, user.email)
    if user.role == "owner":
        _seal_legacy(app, opened)
    s = app.settings

    def init(conn):
        if repo.get_profile(conn) is None:
            repo.upsert_profile(conn, display_name=display_name, timezone=s.tz_default)

    app.vault(opened).write(init)
    _put(app, user.aid, rec)
    return opened


def unlock(app: App, user: User, recovery_key: str) -> User:
    rec = get(app, user.aid) or {}
    if not rec.get("check"):
        raise AccountError(409, "vault_new", "Set up your recovery key first.")
    try:
        vk = sealed.vault_key(sealed.parse_recovery_key(recovery_key))
    except ValueError as exc:
        raise AccountError(403, "wrong_key", str(exc)) from None
    if not hmac.compare_digest(sealed.key_check(vk), rec["check"]):
        raise AccountError(403, "wrong_key", "That recovery key doesn't open this account.")
    return account_user(user.role, user.aid, vk, user.email)


def reset(app: App, user: User) -> None:
    """A lost key: forget it and start over. The old sealed folder can't be found or opened any
    more (its name comes from the lost key); it's never read again."""
    rec = get(app, user.aid) or {}
    rec.pop("check", None)
    _put(app, user.aid, rec)


def _seal_legacy(app: App, opened: User) -> int:
    """Move the owner's plain pre-DB-32 data (local dev) into the sealed folder."""
    dest = app.store_for(opened)
    n = 0
    for old_prefix in LEGACY_OWNER:
        for key in app.store.list(old_prefix):
            b = app.store.get(key)
            if b is None or sealed.is_sealed(b.data):
                continue
            rel = key[len(old_prefix) :]
            new = f"{opened.prefix}{'traces/' if old_prefix.startswith('traces/') else ''}{rel}"
            dest.put(new, b.data)
            app.store.delete(key)
            n += 1
    app.vaults.forget(OWNER.vault_key)
    return n


def delete(app: App, user: User) -> int:
    """Delete an account's data and its account record (the owner keeps a bare record)."""
    n = app.store.delete_prefix(user.prefix) if user.prefix else 0
    if user.role == "user":
        app.store.delete(_key(user.aid))
    else:
        reset(app, user)
    app.vaults.forget(user.vault_key)
    return n
