"""Hybrid retrieval over the vault (plan 8.4).

1. vector search: the query embedding vs `docs` (sqlite-vec when loaded and in sync, else numpy)
2. keyword search: FTS5 BM25
3. Reciprocal Rank Fusion (k = 60); a date window boosts by default, filters when `hard_date`
4. the caller abstains when the best cosine is below ABSTAIN_COSINE and nothing matched by keyword

Embedding (network) happens in `embed_query` / `index`, outside any vault write.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from daybook.memory import db, repo
from daybook.memory.embed import Embedder, from_blob, to_blob
from daybook.memory.vault import Vault

log = logging.getLogger(__name__)
RRF_K = 60
_WORD = re.compile(r"[A-Za-z0-9]{2,}")
_FTS_STOP = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "did",
    "was",
    "that",
    "this",
    "about",
    "my",
    "me",
    "on",
    "in",
    "of",
    "to",
    "it",
    "is",
    "at",
    "by",
    "from",
    "when",
    "how",
    "do",
    "last",
    "week",
    "day",
    "change",
    "actually",
    "not",
}


@dataclass
class Hit:
    doc_id: int
    doc_type: str
    ref_id: int
    occurred_on: str | None
    score: float
    cosine: float = 0.0
    keyword_rank: int | None = None


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    best_cosine: float = 0.0
    keyword_hits: int = 0
    used_vec: bool = False


def _fts_query(q: str) -> str | None:
    words = [w.lower() for w in _WORD.findall(q) if w.lower() not in _FTS_STOP]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


def _vector_scores(
    conn: sqlite3.Connection, qvec: np.ndarray, model: str, k: int, doc_types: tuple[str, ...]
) -> tuple[list[tuple[int, float]], bool]:
    rows = conn.execute(
        f"SELECT id, embedding FROM docs WHERE embedding IS NOT NULL AND embed_model = ?"
        f" AND doc_type IN ({','.join('?' * len(doc_types))})",
        (model, *doc_types),
    ).fetchall()
    if not rows:
        return [], False
    if db.vec_enabled(conn):
        n_vec = conn.execute("SELECT count(*) FROM docs_vec").fetchone()[0]
        n_docs = conn.execute(
            "SELECT count(*) FROM docs WHERE embedding IS NOT NULL AND embed_model = ?", (model,)
        ).fetchone()[0]
        if n_vec == n_docs and n_vec > 0:
            wanted = {r["id"] for r in rows}
            res = conn.execute(
                "SELECT rowid, distance FROM docs_vec WHERE embedding MATCH ? AND k = ?",
                (qvec.astype(np.float32).tobytes(), min(n_vec, max(k * 3, 50))),
            ).fetchall()
            # unit vectors: cosine = 1 - d^2 / 2
            out = [(r[0], 1 - (r[1] ** 2) / 2) for r in res if r[0] in wanted]
            return out[:k], True
    ids = [r["id"] for r in rows]
    mat = np.stack([from_blob(r["embedding"]) for r in rows])
    sims = mat @ qvec
    order = np.argsort(-sims)[:k]
    return [(ids[i], float(sims[i])) for i in order], False


def search(
    conn: sqlite3.Connection,
    qvec: list[float] | np.ndarray,
    query: str,
    model: str,
    date_window: tuple[str, str] | None = None,
    hard_date: bool = False,
    project_ids: list[int] | None = None,
    k: int = 10,
    doc_types: tuple[str, ...] = ("item",),
) -> SearchResult:
    q = np.asarray(qvec, dtype=np.float32)
    vec, used_vec = _vector_scores(conn, q, model, 20, doc_types)
    kw: list[int] = []
    fq = _fts_query(query)
    if fq:
        try:
            kw = [
                r[0]
                for r in conn.execute(
                    "SELECT f.rowid FROM docs_fts f JOIN docs d ON d.id = f.rowid"
                    f" WHERE docs_fts MATCH ? AND d.doc_type IN ({','.join('?' * len(doc_types))})"
                    " ORDER BY bm25(docs_fts) LIMIT 20",
                    (fq, *doc_types),
                )
            ]
        except sqlite3.OperationalError as exc:
            log.info("fts query failed: %s", exc)
    scores: dict[int, float] = {}
    cos: dict[int, float] = dict(vec)
    for rank, (doc_id, _) in enumerate(vec):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (RRF_K + rank + 1)
    kw_rank = {}
    for rank, doc_id in enumerate(kw):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (RRF_K + rank + 1)
        kw_rank[doc_id] = rank
    if not scores:
        return SearchResult(used_vec=used_vec)
    meta = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, doc_type, ref_id, occurred_on, project_id FROM docs"
            f" WHERE id IN ({','.join('?' * len(scores))})",
            list(scores),
        )
    }
    hits = []
    for doc_id, s in scores.items():
        m = meta.get(doc_id)
        if m is None:
            continue
        in_window = (
            date_window is not None
            and m["occurred_on"] is not None
            and date_window[0] <= m["occurred_on"] <= date_window[1]
        )
        if date_window and hard_date and not in_window:
            continue
        if in_window:
            s += 1 / (RRF_K + 1)  # as if ranked first in a third list
        if project_ids and m["project_id"] in project_ids:
            s += 0.5 / (RRF_K + 1)
        hits.append(
            Hit(
                doc_id,
                m["doc_type"],
                m["ref_id"],
                m["occurred_on"],
                s,
                cos.get(doc_id, 0.0),
                kw_rank.get(doc_id),
            )
        )
    hits.sort(key=lambda h: -h.score)
    return SearchResult(
        hits=hits[:k],
        best_cosine=max((c for _, c in vec), default=0.0),
        keyword_hits=len(kw),
        used_vec=used_vec,
    )


async def embed_query(embedder: Embedder, query: str) -> list[float]:
    return (await embedder.embed([query], "query"))[0]


async def index(vault: Vault, embedder: Embedder, item_ids: list[int] | None = None) -> int:
    """Re-embed stale docs (items, and digests when item_ids is None). Returns how many."""
    with vault.read() as conn:
        todo, drop = repo.docs_needing_update(conn, item_ids)
    if not todo and not drop:
        return 0
    vectors = await embedder.embed([d["text"] for d in todo], "document") if todo else []

    def write(conn: sqlite3.Connection) -> int:
        for ref_id in drop:
            conn.execute("DELETE FROM docs WHERE doc_type = 'item' AND ref_id = ?", (ref_id,))
        for d, v in zip(todo, vectors, strict=True):
            doc_id = repo.upsert_doc(conn, d, to_blob(v), embedder.model_name)
            if db.vec_enabled(conn):
                conn.execute("DELETE FROM docs_vec WHERE rowid = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO docs_vec (rowid, embedding) VALUES (?, ?)", (doc_id, to_blob(v))
                )
        if db.vec_enabled(conn):  # drop vectors whose doc is gone
            conn.execute("DELETE FROM docs_vec WHERE rowid NOT IN (SELECT id FROM docs)")
        return len(todo)

    return vault.write(write)
