"""Blob storage with ETags and conditional writes (plan 5.2, 12.2).

`LocalBlobStore` keeps files under a directory and mimics S3's `If-Match` / `If-None-Match`
semantics, so the vault, session files and counters behave the same locally and in Lambda.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PreconditionFailed(Exception):
    """The object changed since it was read (HTTP 412)."""


@dataclass
class Blob:
    data: bytes
    etag: str


class BlobStore(Protocol):
    def get(self, key: str) -> Blob | None: ...

    def head(self, key: str) -> str | None: ...

    def put(
        self,
        key: str,
        data: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
        content_type: str = "application/octet-stream",
    ) -> str: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def list(self, prefix: str) -> list[str]: ...


def _etag(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324 (same as S3's single-part ETag)


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"bad key {key!r}")
        return p

    def get(self, key: str) -> Blob | None:
        p = self._path(key)
        if not p.is_file():
            return None
        data = p.read_bytes()
        return Blob(data, _etag(data))

    def head(self, key: str) -> str | None:
        b = self.get(key)
        return b.etag if b else None

    def put(self, key, data, *, if_match=None, if_none_match=False, content_type=""):
        p = self._path(key)
        with self._lock:
            current = _etag(p.read_bytes()) if p.is_file() else None
            if if_none_match and current is not None:
                raise PreconditionFailed(key)
            if if_match is not None and current != if_match:
                raise PreconditionFailed(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return _etag(data)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for key in self.list(prefix):
            self.delete(key)
            n += 1
        return n

    def list(self, prefix: str) -> list[str]:
        base = self.root
        out = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.endswith(".tmp"):
                key = p.relative_to(base).as_posix()
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)


class S3BlobStore:
    """S3 with conditional writes (`IfMatch` / `IfNoneMatch` on PutObject)."""

    def __init__(self, bucket: str):
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def get(self, key: str) -> Blob | None:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=key)
        except self.s3.exceptions.NoSuchKey:
            return None
        return Blob(r["Body"].read(), r["ETag"].strip('"'))

    def head(self, key: str) -> str | None:
        from botocore.exceptions import ClientError

        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ETag"].strip('"')
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, key, data, *, if_match=None, if_none_match=False, content_type=""):
        from botocore.exceptions import ClientError

        kwargs = dict(Bucket=self.bucket, Key=key, Body=data)
        if content_type:
            kwargs["ContentType"] = content_type
        if if_match is not None:
            kwargs["IfMatch"] = f'"{if_match}"'
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        try:
            r = self.s3.put_object(**kwargs)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
                raise PreconditionFailed(key) from exc
            raise
        return r["ETag"].strip('"')

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for start in range(0, len(keys), 1000):
            chunk = [{"Key": k} for k in keys[start : start + 1000]]
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": chunk})
        return len(keys)

    def list(self, prefix: str) -> list[str]:
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            out.extend(o["Key"] for o in page.get("Contents", []))
        return out


_store: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _store
    if _store is None:
        from daybook.settings import get_settings

        s = get_settings()
        _store = S3BlobStore(s.daybook_bucket) if s.daybook_blob == "s3" else LocalBlobStore(
            s.daybook_data_dir / "blobs"
        )
    return _store


def set_blob_store(store: BlobStore | None) -> None:
    """Tests swap the store."""
    global _store
    _store = store
