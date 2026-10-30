"""Opening a vault file: schema, migrations, and the optional sqlite-vec extension (plan 8.3).

Every DB access in the app goes through a connection made here (parameterized queries only).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"
USER_VERSION = 1


def _load_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:  # spike S1 fallback: numpy brute force
        log.info("sqlite-vec unavailable: %s", exc)
        return False


def connect(path: Path | str, embed_dim: int = 768) -> sqlite3.Connection:
    """Open (and create if needed) a vault. Sets `conn.has_vec` style info via `vec_enabled()`."""
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")  # one self-contained file to upload to S3
    has_vec = _load_vec(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < USER_VERSION:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute(f"PRAGMA user_version = {USER_VERSION}")
    if has_vec:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS docs_vec USING vec0(embedding float[{embed_dim}])"
        )
    _VEC[id(conn)] = has_vec
    return conn


_VEC: dict[int, bool] = {}


def vec_enabled(conn: sqlite3.Connection) -> bool:
    return _VEC.get(id(conn), False)


def close(conn: sqlite3.Connection) -> None:
    _VEC.pop(id(conn), None)
    conn.close()
