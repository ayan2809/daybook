"""The reminder record (DB-32): what a reminder needs while the user is away, and nothing else.

`users/<folder>/notify.json` holds the push subscriptions, the reminder times and timezone, the last
day a reflection was saved, how many questions are open, and which nudges went out today. It holds
no words the user said. A scheduled reminder fires with no user present, so this one file is
sealed with a server key rather than the user's; everything else in the folder stays the user's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from daybook.storage.blob import PreconditionFailed
from daybook.storage.sealed import SealedStore, server_key

FOLDER = re.compile(r"^v[0-9a-f]{24}$")


def _store(app) -> SealedStore:
    return SealedStore(app.store, server_key(app.settings.session_signing_key, "notify"))


def key(prefix: str) -> str:
    return f"{prefix}notify.json"


def load(app, prefix: str) -> dict:
    b = _store(app).get(key(prefix))
    return json.loads(b.data) if b else {}


def update(app, prefix: str, fn: Callable[[dict], None]) -> dict:
    store = _store(app)
    for _ in range(5):
        b = store.get(key(prefix))
        rec = json.loads(b.data) if b else {}
        fn(rec)
        try:
            store.put(
                key(prefix),
                json.dumps(rec).encode(),
                if_match=b.etag if b else None,
                if_none_match=b is None,
            )
            return rec
        except PreconditionFailed:
            continue
    return rec


def for_user(app, user, fn: Callable[[dict], None]) -> None:
    """Accounts only; the demo has no reminders."""
    if user.is_account and user.key is not None:
        update(app, user.prefix, fn)


def prefixes(app) -> list[str]:
    out = []
    for k in app.store.list("users/"):
        parts = k.split("/")
        if len(parts) == 3 and parts[2] == "notify.json" and FOLDER.match(parts[1]):
            out.append(f"users/{parts[1]}/")
    return out


def prefix_for(folder: str) -> str | None:
    return f"users/{folder}/" if FOLDER.match(folder or "") else None
