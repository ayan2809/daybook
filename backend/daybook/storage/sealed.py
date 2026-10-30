"""Sealed storage (DB-32): every object in an account's folder is encrypted with that account's key.

The key hierarchy:

    recovery key (160 random bits, made in the browser, held only by the user)
      └─ vault key  = HKDF-SHA256(recovery key, "daybook vault-key v1")     never stored
           ├─ blob key    = HKDF(vault key, "blob v1")       AES-256-GCM for every object
           ├─ folder id   = HMAC(vault key, "folder v1")     users/<folder id>/ (unlinkable)
           └─ key check   = HMAC(vault key, "check v1")      stored in the account record

An object is `DBK1 | 12-byte nonce | ciphertext+tag`, with the object's key as associated data, so
a sealed blob can't be moved to another path or another account. ETags are the ciphertext's, so
If-Match writes work unchanged. Server keys (cookie wrapping, account ids, worker hand-off, the
reminder record) are derived from the session signing key, which lives only in SSM.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from daybook.storage.blob import Blob, BlobStore

MAGIC = b"DBK1"
RECOVERY_BYTES = 20  # 160 bits → 32 base32 characters


class SealError(Exception):
    """A blob that isn't sealed, or that doesn't open with this key."""


def hkdf(secret: bytes, info: str, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=b"daybook", info=info.encode()
    ).derive(secret)


def _mac(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ the user's keys


def parse_recovery_key(text: str) -> bytes:
    """`ABCD-EFGH-…` (base32, 32 characters; dashes, spaces and case don't matter)."""
    s = "".join(ch for ch in text.upper() if ch.isalnum())
    if len(s) != 32:
        raise ValueError("A recovery key has 32 letters and digits.")
    try:
        raw = base64.b32decode(s)
    except Exception:
        raise ValueError("That isn't a Daybook recovery key.") from None
    if len(raw) != RECOVERY_BYTES:
        raise ValueError("That isn't a Daybook recovery key.")
    return raw


def format_recovery_key(raw: bytes) -> str:
    s = base64.b32encode(raw).decode()
    return "-".join(s[i : i + 4] for i in range(0, len(s), 4))


def vault_key(recovery: bytes) -> bytes:
    return hkdf(recovery, "daybook vault-key v1")


def folder_id(vk: bytes) -> str:
    return "v" + _mac(vk, "folder v1")[:24]


def key_check(vk: bytes) -> str:
    return _mac(vk, "check v1")[:32]


# ------------------------------------------------------------------ server keys


def server_key(signing_key: str, purpose: str) -> bytes:
    return hkdf(signing_key.encode(), f"daybook server {purpose} v1")


def account_id(signing_key: str, google_sub: str) -> str:
    """A keyed hash of Google's opaque account id: no email or name is ever stored."""
    return "a" + _mac(server_key(signing_key, "account-id"), f"google:{google_sub}")[:24]


def wrap(key: bytes, data: bytes, aad: str) -> str:
    """Small secrets (a vault key in a cookie or a worker event), URL-safe text."""
    return base64.urlsafe_b64encode(seal(key, data, aad)).decode()


def unwrap(key: bytes, text: str, aad: str) -> bytes:
    try:
        return open_sealed(key, base64.urlsafe_b64decode(text.encode()), aad)
    except Exception as exc:
        raise SealError("bad wrapped key") from exc


# ------------------------------------------------------------------ AES-256-GCM


def seal(key: bytes, data: bytes, aad: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad.encode())


def open_sealed(key: bytes, blob: bytes, aad: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise SealError("not sealed")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], aad.encode())
    except Exception as exc:
        raise SealError("doesn't open with this key") from exc


def is_sealed(data: bytes) -> bool:
    return data.startswith(MAGIC)


class SealedStore:
    """A BlobStore that encrypts on put and decrypts on get. Keys (paths) stay as they are."""

    def __init__(self, inner: BlobStore, key: bytes):
        self.inner = inner
        self.key = key

    def get(self, key: str) -> Blob | None:
        b = self.inner.get(key)
        if b is None:
            return None
        return Blob(open_sealed(self.key, b.data, key), b.etag)

    def head(self, key: str) -> str | None:
        return self.inner.head(key)

    def put(self, key, data, *, if_match=None, if_none_match=False, content_type=""):
        return self.inner.put(
            key,
            seal(self.key, data, key),
            if_match=if_match,
            if_none_match=if_none_match,
            content_type="application/octet-stream",
        )

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def delete_prefix(self, prefix: str) -> int:
        return self.inner.delete_prefix(prefix)

    def list(self, prefix: str) -> list[str]:
        return self.inner.list(prefix)


def sealed_for(inner: BlobStore, vk: bytes) -> SealedStore:
    return SealedStore(inner, hkdf(vk, "blob v1"))
