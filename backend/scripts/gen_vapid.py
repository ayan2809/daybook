"""Generate a VAPID key pair for Web Push and write it to the root .env (plan 10, 12.3)."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from scripts.owner_key import set_env


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


if __name__ == "__main__":
    key = ec.generate_private_key(ec.SECP256R1())
    priv = b64url(key.private_numbers().private_value.to_bytes(32, "big"))
    pub = b64url(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    set_env("VAPID_PRIVATE_KEY", priv)
    set_env("VAPID_PUBLIC_KEY", pub)
    print("VAPID keys written to .env (public key):", pub)
