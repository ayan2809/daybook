"""A1 schema, A2/A3 validation and deltas, A8 vault If-Match, A10 retrieval."""

import sqlite3

import pytest

from daybook.memory import db, repo, retrieval
from daybook.memory.embed import FakeEmbedder
from daybook.memory.validate import (
    compute_deltas,
    metric_display,
    number_supported,
    numbers_unsupported,
    quote_in,
)
from daybook.memory.vault import Vault
from daybook.storage.blob import LocalBlobStore, PreconditionFailed


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    db.close(c)


def _item(conn, **kw):
    fields = dict(
        kind="achievement",
        occurred_on="2026-10-20",
        title="Ledger migration",
        what="Migrated the ledger",
        source_quote="migrated the ledger",
        status="active",
    )
    fields.update(kw)
    return repo.create_item(conn, fields)


# A1
def test_schema_checks(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _item(conn, bullet="too short")
    with pytest.raises(sqlite3.IntegrityError):
        _item(conn, kind="banana")
    item = _item(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO metrics (item_id, name, category, unit, better_when, source_text)"
            " VALUES (?, 'x', 'other', 'ms', 'lower', 'x')",
            (item,),
        )


# A2
def test_quote_and_number_checks():
    turns = [
        "I finally shipped the webhook retry worker today.",
        "From about 1,200 failed deliveries a day to under 100.",
    ]
    assert quote_in("shipped the webhook retry worker", turns)
    assert quote_in("Shipped  the webhook retry worker.", turns)
    assert not quote_in("shipped the payment retry worker", turns)
    assert number_supported(1200, turns) and number_supported(100, turns)
    assert not number_supported(35, turns)
    assert numbers_unsupported("down 35% from 1,200", turns) == ["35%"]
    assert number_supported(18000, ["about 18k users"])


# A3
def test_delta_math():
    d = compute_deltas(120, 45, None, "ms", "lower")
    assert (d.delta_abs, d.delta_pct, d.is_derived) == (-75, -62.5, True)
    d = compute_deltas(None, None, 35, "%", "lower", "error rate dropped 35%")
    assert (d.delta_pct, d.is_derived, d.baseline) == (-35, False, None)
    d = compute_deltas(None, 4, None, "x", "higher", "4x throughput")
    assert (d.baseline, d.final, d.delta_pct) == (1, 4, 300)
    assert (
        metric_display({"baseline": 120, "final": 45, "unit": "ms", "delta_pct": -62.5})
        == "120 → 45 ms (−62.5%)"
    )


def test_item_versions_and_restore(conn):
    iid = repo.create_item(
        conn,
        dict(
            kind="achievement",
            occurred_on="2026-10-20",
            title="Ledger migration",
            what="Migrated the ledger",
            source_quote="migrated the ledger",
            status="active",
        ),
        metrics=[
            {
                "name": "reconciliation time",
                "category": "time",
                "unit": "%",
                "delta_pct": -50,
                "better_when": "lower",
                "source_text": "50% faster",
            }
        ],
    )
    mid = repo.get_item(conn, iid)["metrics"][0]["id"]
    v = repo.update_item(
        conn,
        iid,
        {"result": "40% faster"},
        "user_voice",
        metric_updates=[{"id": mid, "delta_pct": -40, "source_text": "40%"}],
    )
    assert v == 2
    item = repo.get_item(conn, iid)
    assert item["metrics"][0]["delta_pct"] == -40 and item["result"] == "40% faster"
    repo.restore_version(conn, iid, 1)
    item = repo.get_item(conn, iid)
    assert item["version"] == 3 and item["result"] is None
    assert item["metrics"][0]["delta_pct"] == -50
    assert [v["version"] for v in repo.item_versions(conn, iid)] == [3, 2, 1]


def test_pending_lifecycle(conn):
    iid = _item(conn)
    pid = repo.create_pending(conn, iid, "metric", "What was p99 before and after, in ms?")
    assert repo.create_pending(conn, iid, "metric", "dup?") == pid
    repo.mark_asked(conn, [pid])
    repo.mark_asked(conn, [pid])
    assert repo.get_pending(conn, pid)["status"] == "open"
    repo.mark_asked(conn, [pid])
    assert repo.get_pending(conn, pid)["status"] == "expired"


# A8
def test_vault_if_match_retries(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs")
    a = Vault(store, "users/u/memory.db", tmp_path / "ca")
    b = Vault(store, "users/u/memory.db", tmp_path / "cb")
    a.write(lambda c: repo.upsert_profile(c, display_name="A"))
    with b.read() as c:
        assert repo.get_profile(c)["display_name"] == "A"
    a.write(lambda c: repo.put_setting(c, "x", 1))  # b's copy is now stale
    calls = []

    def fn(c):
        calls.append(1)
        repo.put_setting(c, "y", 2)

    b.etag = b.etag  # stale on purpose
    b.write(fn)
    with a.read() as c:
        assert repo.get_setting(c, "x") == 1 and repo.get_setting(c, "y") == 2
    # a real conflict: the blob changes between b's refresh and its PUT
    orig_put = store.put
    state = {"n": 0}

    def flaky_put(key, data, **kw):
        if state["n"] == 0:
            state["n"] += 1
            orig_put(
                key,
                store.get(key).data + b"",
            )  # same content, new write
            raise PreconditionFailed(key)
        return orig_put(key, data, **kw)

    store.put = flaky_put
    calls.clear()
    b.write(fn)
    assert len(calls) == 2


# A10
async def test_hybrid_retrieval(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs")
    v = Vault(store, "users/u/memory.db", tmp_path / "c")
    emb = FakeEmbedder()

    def seed(c):
        _item(
            c,
            title="Ledger migration",
            what="Migrated the ledger tables to Postgres",
            occurred_on="2026-10-20",
        )
        _item(
            c,
            title="Cache cleanup",
            what="Removed stale cache keys in the ledger API",
            occurred_on="2026-10-21",
        )
        _item(
            c,
            title="Mentored intern",
            what="Helped the intern debug her first PR",
            kind="soft_skill",
            occurred_on="2026-10-15",
        )

    v.write(seed)
    assert await retrieval.index(v, emb) == 3
    assert await retrieval.index(v, emb) == 0  # nothing stale
    q = "the ledger migration last Tuesday"
    qv = await retrieval.embed_query(emb, q)
    with v.read() as c:
        r = retrieval.search(c, qv, q, emb.model_name, date_window=("2026-10-20", "2026-10-20"))
        top_vec = [h.ref_id for h in r.hits[:3]]
        assert r.hits[0].ref_id == 1
        # numpy fallback gives the same top 3 as sqlite-vec
        db._VEC[id(c)] = False
        r2 = retrieval.search(c, qv, q, emb.model_name, date_window=("2026-10-20", "2026-10-20"))
        assert [h.ref_id for h in r2.hits[:3]] == top_vec and not r2.used_vec
    assert r.used_vec
    qv = await retrieval.embed_query(emb, "presentation to the CFO")
    with v.read() as c:
        r = retrieval.search(c, qv, "presentation to the CFO", emb.model_name)
        assert r.keyword_hits == 0 and r.best_cosine < 0.35
