"""The SQLite vault lives as one file in blob storage (plan 5.2, 12.2).

open_read(): keep a local copy in a cache dir plus the ETag it came from; re-download only when the
            blob's ETag changed.
write(fn):  fresh copy → BEGIN → fn(conn) → COMMIT → PUT If-Match <old ETag>. On 412, re-download and
            run fn again (fn must be re-runnable), at most 3 tries. Network calls (LLM, embeddings)
            must happen before write(), never inside fn.

A `Vault` is identified by its blob key (`users/owner/memory.db`, `demo/<id>/memory.db`).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from daybook.memory import db
from daybook.storage.blob import BlobStore, PreconditionFailed

log = logging.getLogger(__name__)
T = TypeVar("T")

MAX_CACHED = 20  # demo sandboxes: keep at most 20 local copies (LRU)


class Vault:
    def __init__(self, store: BlobStore, key: str, cache_dir: Path, embed_dim: int = 768):
        self.store = store
        self.key = key
        self.embed_dim = embed_dim
        self.local = cache_dir / key.replace("/", "__")
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.unlink(missing_ok=True)  # a copy left by an earlier process has an unknown ETag
        self.etag: str | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ sync

    def _refresh(self) -> None:
        """Make the local copy match the blob (or create an empty vault if there is none)."""
        remote = self.store.head(self.key)
        if remote is None:
            if not self.local.exists() or self.etag is not None:
                self.local.unlink(missing_ok=True)
                db.close(db.connect(self.local, self.embed_dim))  # empty schema
            self.etag = None
            return
        if remote == self.etag and self.local.exists():
            return
        blob = self.store.get(self.key)
        if blob is None:  # deleted between head and get
            self.etag = None
            self.local.unlink(missing_ok=True)
            db.close(db.connect(self.local, self.embed_dim))
            return
        tmp = self.local.with_suffix(".dl")
        tmp.write_bytes(blob.data)
        tmp.replace(self.local)
        self.etag = blob.etag

    @contextmanager
    def read(self):
        """A connection to the current local copy. Don't write through it."""
        with self._lock:
            self._refresh()
            conn = db.connect(self.local, self.embed_dim)
            try:
                yield conn
            finally:
                db.close(conn)

    def write(self, fn: Callable[[sqlite3.Connection], T], attempts: int = 3) -> T:
        with self._lock:
            for attempt in range(attempts):
                self._refresh()
                work = Path(tempfile.mkdtemp()) / "memory.db"
                shutil.copyfile(self.local, work)
                conn = db.connect(work, self.embed_dim)
                try:
                    conn.execute("BEGIN")
                    result = fn(conn)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    db.close(conn)
                    shutil.rmtree(work.parent, ignore_errors=True)
                    raise
                db.close(conn)
                data = work.read_bytes()
                try:
                    if self.etag is None:
                        new_etag = self.store.put(self.key, data, if_none_match=True)
                    else:
                        new_etag = self.store.put(self.key, data, if_match=self.etag)
                except PreconditionFailed:
                    log.info("vault %s changed underneath us (attempt %d); retrying", self.key, attempt)
                    shutil.rmtree(work.parent, ignore_errors=True)
                    self.etag = "stale"  # force a re-download
                    continue
                shutil.move(str(work), self.local)
                shutil.rmtree(work.parent, ignore_errors=True)
                self.etag = new_etag
                return result
            raise PreconditionFailed(f"{self.key}: gave up after {attempts} conflicting writes")

    def raw_bytes(self) -> bytes:
        with self._lock:
            self._refresh()
            return self.local.read_bytes()


class VaultRegistry:
    """One Vault object per key per process, with an LRU cap on local copies."""

    def __init__(self, store: BlobStore, cache_dir: Path, embed_dim: int = 768):
        self.store = store
        self.cache_dir = cache_dir
        self.embed_dim = embed_dim
        self._vaults: OrderedDict[str, Vault] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Vault:
        with self._lock:
            v = self._vaults.get(key)
            if v is None:
                v = Vault(self.store, key, self.cache_dir, self.embed_dim)
                self._vaults[key] = v
            self._vaults.move_to_end(key)
            while len(self._vaults) > MAX_CACHED:
                _, old = self._vaults.popitem(last=False)
                old.local.unlink(missing_ok=True)
            return v

    def forget(self, key: str) -> None:
        with self._lock:
            v = self._vaults.pop(key, None)
            if v:
                v.local.unlink(missing_ok=True)
