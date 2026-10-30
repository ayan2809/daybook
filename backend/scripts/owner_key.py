"""Generate the owner key (plan 14.1): 256 random bits, printed once; only its SHA-256 is stored.

Locally the hash goes into the root .env (OWNER_KEY_HASH). In production `make secrets` will put it in
SSM (/daybook/owner_key_hash).
"""

from __future__ import annotations

import hashlib
import secrets

from daybook.settings import REPO_ROOT


def set_env(key: str, value: str) -> None:
    env = REPO_ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)


if __name__ == "__main__":
    key = secrets.token_urlsafe(32)
    set_env("OWNER_KEY_HASH", hashlib.sha256(key.encode()).hexdigest())
    if "SESSION_SIGNING_KEY=" not in (REPO_ROOT / ".env").read_text():
        set_env("SESSION_SIGNING_KEY", secrets.token_urlsafe(48))
    print("Owner key (shown once; paste it in Settings → Sign in):")
    print(key)
