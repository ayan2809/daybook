"""Build data/seed/demo-memory.db from data/seed/meera.json (plan 15).

Embeddings come from the configured embedder (DAYBOOK_EMBED=fake|gemini). The demo must be seeded
with the same embedder the app uses, or vector search is skipped for those docs.

    uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from daybook.engine.app import App
from daybook.engine.demo import SEED_DB, build_seed_db
from daybook.memory import db, retrieval
from daybook.memory.vault import Vault
from daybook.storage.blob import LocalBlobStore


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    conn, _ = build_seed_db(tmp / "seed.db")
    db.close(conn)
    store = LocalBlobStore(tmp / "blobs")
    store.put("seed/memory.db", (tmp / "seed.db").read_bytes())
    app = App(store=store, tts=None)
    vault = Vault(store, "seed/memory.db", tmp / "cache")
    n = await retrieval.index(vault, app.embedder)
    SEED_DB.write_bytes(store.get("seed/memory.db").data)
    shutil.rmtree(tmp, ignore_errors=True)
    print(
        f"wrote {SEED_DB} ({SEED_DB.stat().st_size // 1024} KB, {n} docs embedded with "
        f"{app.embedder.model_name})"
    )


if __name__ == "__main__":
    asyncio.run(main())
