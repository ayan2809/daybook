"""Accounts (DB-29): Google sign-in, where the first sign-in is the sign-up.

Each account gets its own vault under `users/<uid>/`; the uid is a hash of Google's stable `sub`,
so no Google id is stored in a key. `accounts/<uid>.json` holds the email and name. Ayan's email
(OWNER_EMAILS) maps to the existing `users/owner/` vault and the admin pages.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from daybook.engine.app import OWNER, App, User, account_user
from daybook.memory import repo

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class AccountError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _emails(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def uid_for(sub: str) -> str:
    return "g" + hashlib.sha256(f"google:{sub}".encode()).hexdigest()[:24]


def get(app: App, uid: str) -> dict | None:
    b = app.store.get(f"accounts/{uid}.json")
    return json.loads(b.data) if b else None


def all_uids(app: App) -> list[str]:
    return [k.split("/", 1)[1][:-5] for k in app.store.list("accounts/") if k.endswith(".json")]


def verify_google(credential: str, client_id: str) -> dict:
    """Check a Google ID token: signature (Google's keys), audience, expiry and issuer."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, g_requests.Request(), client_id)


def sign_in(app: App, claims: dict, now: datetime | None = None) -> User:
    """Map verified Google claims to a user, creating the account on first sign-in."""
    s = app.settings
    email = str(claims.get("email") or "").lower()
    if claims.get("iss") not in GOOGLE_ISSUERS or not claims.get("sub"):
        raise AccountError(401, "unauthorized", "That sign-in didn't come from Google.")
    if not email or not claims.get("email_verified"):
        raise AccountError(401, "unauthorized", "Your Google email isn't verified.")
    if email in _emails(s.owner_emails):
        return account_user(OWNER.uid, email)
    allowed = _emails(s.allowed_emails)
    if allowed and email not in allowed:
        raise AccountError(403, "not_invited", "This Daybook is invite-only for now.")
    uid = uid_for(str(claims["sub"]))
    if get(app, uid) is None:
        if len(all_uids(app)) >= s.max_accounts:
            raise AccountError(403, "signups_closed", "Sign-ups are full right now.")
        now = now or datetime.now(UTC)
        record = {
            "uid": uid,
            "email": email,
            "name": claims.get("name"),
            "created_at": now.isoformat(),
        }
        app.store.put(
            f"accounts/{uid}.json", json.dumps(record).encode(), content_type="application/json"
        )
        first = claims.get("given_name") or claims.get("name")
        user = account_user(uid, email)

        def init(conn):
            if repo.get_profile(conn) is None:
                repo.upsert_profile(conn, display_name=first, timezone=s.tz_default)

        app.vault(user).write(init)
    return account_user(uid, email)


def delete(app: App, user: User) -> int:
    """Delete a user's data. A Google account also loses its account record."""
    n = app.store.delete_prefix(user.prefix)
    n += app.store.delete_prefix(f"traces/{user.uid}/")
    if user.role == "user":
        app.store.delete(f"accounts/{user.uid}.json")
    app.vaults.forget(user.vault_key)
    return n
