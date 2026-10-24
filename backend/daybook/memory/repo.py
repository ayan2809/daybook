"""Data access for one vault connection. Parameterized queries only (plan 16).

Items are versioned: every create/update/restore writes a snapshot to `memory_item_versions`, so
edits never delete anything (plan 7.4).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from daybook.memory.validate import compute_deltas, metric_display

ITEM_FIELDS = (
    "kind",
    "status",
    "occurred_on",
    "project_id",
    "title",
    "what",
    "how",
    "result",
    "bullet",
    "scope",
    "impact",
    "quantifiable",
    "ownership",
    "source_quote",
    "source_turn_seqs",
    "validation_note",
    "merged_into",
)
EDITABLE = (
    "title",
    "what",
    "how",
    "result",
    "bullet",
    "kind",
    "ownership",
    "occurred_on",
    "scope",
    "impact",
    "quantifiable",
    "status",
    "project_id",
    "validation_note",
)
METRIC_FIELDS = (
    "name",
    "category",
    "unit",
    "baseline",
    "final",
    "delta_abs",
    "delta_pct",
    "better_when",
    "period",
    "approx",
    "is_derived",
    "source_text",
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ profile and settings


def get_profile(conn: sqlite3.Connection) -> dict | None:
    r = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return dict(r) if r else None


def upsert_profile(conn: sqlite3.Connection, **fields: Any) -> None:
    cur = get_profile(conn)
    if cur is None:
        conn.execute(
            "INSERT INTO profile (id, display_name, timezone, org, role_title, created_at)"
            " VALUES (1, ?, ?, ?, ?, ?)",
            (
                fields.get("display_name"),
                fields.get("timezone") or "Asia/Kolkata",
                fields.get("org"),
                fields.get("role_title"),
                now_iso(),
            ),
        )
        return
    for k in ("display_name", "timezone", "org", "role_title"):
        if k in fields and fields[k] is not None:
            conn.execute(f"UPDATE profile SET {k} = ? WHERE id = 1", (fields[k],))


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def put_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


# ------------------------------------------------------------------ projects and skills


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    out = rows(conn.execute("SELECT id, name, aliases, kind, status FROM projects ORDER BY name"))
    for p in out:
        p["aliases"] = json.loads(p["aliases"])
    return out


def project_id_for(conn: sqlite3.Connection, name: str | None, create: bool = True) -> int | None:
    if not name or not name.strip():
        return None
    name = name.strip()
    r = conn.execute("SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if r:
        return r["id"]
    for p in list_projects(conn):
        if name.lower() in (a.lower() for a in p["aliases"]):
            return p["id"]
    if not create:
        return None
    cur = conn.execute(
        "INSERT INTO projects (name, created_at) VALUES (?, ?)", (name[:80], now_iso())
    )
    return cur.lastrowid


def skill_id_for(conn: sqlite3.Connection, name: str, category: str = "other") -> int:
    name = name.strip()[:60]
    r = conn.execute("SELECT id FROM skills WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if r:
        return r["id"]
    for s in rows(conn.execute("SELECT id, aliases FROM skills")):
        if name.lower() in (a.lower() for a in json.loads(s["aliases"])):
            return s["id"]
    return conn.execute(
        "INSERT INTO skills (name, category) VALUES (?, ?)", (name, category)
    ).lastrowid


# ------------------------------------------------------------------ sessions and turns


def upsert_session(conn: sqlite3.Connection, row: dict) -> None:
    cols = list(row)
    conn.execute(
        f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        f" ON CONFLICT(id) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id"),
        [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()],
    )


def get_session_row(conn: sqlite3.Connection, sid: str) -> dict | None:
    r = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["stats"] = json.loads(d["stats"])
    d["prompt_versions"] = json.loads(d["prompt_versions"])
    return d


def insert_turns(conn: sqlite3.Connection, sid: str, turns: list[dict]) -> None:
    for t in turns:
        conn.execute(
            "INSERT OR IGNORE INTO turns (session_id, seq, role, text, intent, dictated_chars,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                t["seq"],
                t["role"],
                t["text"],
                t.get("intent"),
                t.get("dictated_chars"),
                t.get("created_at") or now_iso(),
            ),
        )


def reflected_dates(conn: sqlite3.Connection, since: str) -> set[str]:
    return {
        r["local_date"]
        for r in conn.execute(
            "SELECT DISTINCT local_date FROM sessions WHERE local_date >= ?"
            " AND status IN ('ended','finalizing','finalized') AND turn_count > 0",
            (since,),
        )
    }


# ------------------------------------------------------------------ items


def _metric_rows(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM metrics WHERE item_id = ? ORDER BY id", (item_id,)))
    for m in out:
        m["approx"] = bool(m["approx"])
        m["is_derived"] = bool(m["is_derived"])
        m["display"] = metric_display(m)
    return out


def _skills(conn: sqlite3.Connection, item_id: int) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            "SELECT s.name FROM item_skills i JOIN skills s ON s.id = i.skill_id WHERE i.item_id = ?"
            " ORDER BY s.name",
            (item_id,),
        )
    ]


def get_item(conn: sqlite3.Connection, item_id: int, full: bool = True) -> dict | None:
    r = conn.execute(
        "SELECT i.*, p.name AS project_name FROM memory_items i"
        " LEFT JOIN projects p ON p.id = i.project_id WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    return _item_dict(conn, r, full) if r else None


def _item_dict(conn: sqlite3.Connection, r: sqlite3.Row, full: bool = True) -> dict:
    d = dict(r)
    d["source_turn_seqs"] = json.loads(d["source_turn_seqs"])
    d["project"] = (
        {"id": d["project_id"], "name": d.pop("project_name")} if d["project_id"] else None
    )
    d.pop("project_name", None)
    if full:
        d["metrics"] = _metric_rows(conn, d["id"])
        d["skills"] = _skills(conn, d["id"])
        d["open_pending"] = pending_for_item(conn, d["id"])
    return d


def list_items(
    conn: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    project_id: int | None = None,
    kind: str | None = None,
    q: str | None = None,
    include_hidden: bool = False,
    ids: list[int] | None = None,
    limit: int = 500,
) -> list[dict]:
    where, args = [], []
    if not include_hidden:
        where.append("i.status NOT IN ('hidden','merged')")
    if date_from:
        where.append("i.occurred_on >= ?")
        args.append(date_from)
    if date_to:
        where.append("i.occurred_on <= ?")
        args.append(date_to)
    if project_id:
        where.append("i.project_id = ?")
        args.append(project_id)
    if kind:
        where.append("i.kind = ?")
        args.append(kind)
    if q:
        where.append("(i.title LIKE ? OR i.what LIKE ? OR i.result LIKE ? OR i.bullet LIKE ?)")
        args += [f"%{q}%"] * 4
    if ids is not None:
        if not ids:
            return []
        where.append(f"i.id IN ({','.join('?' * len(ids))})")
        args += ids
    sql = (
        "SELECT i.*, p.name AS project_name FROM memory_items i"
        " LEFT JOIN projects p ON p.id = i.project_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY i.occurred_on DESC, i.id DESC LIMIT ?"
    )
    return [_item_dict(conn, r) for r in conn.execute(sql, [*args, limit])]


def snapshot(conn: sqlite3.Connection, item_id: int) -> dict:
    r = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
    snap = {k: r[k] for k in ITEM_FIELDS}
    snap["source_turn_seqs"] = json.loads(snap["source_turn_seqs"])
    snap["metrics"] = [{k: m[k] for k in METRIC_FIELDS} for m in _metric_rows(conn, item_id)]
    snap["skills"] = _skills(conn, item_id)
    return snap


def _write_version(conn, item_id: int, version: int, changed_by: str, reason: str | None) -> None:
    conn.execute(
        "INSERT INTO memory_item_versions (item_id, version, snapshot, changed_by, reason,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, version, json.dumps(snapshot(conn, item_id)), changed_by, reason, now_iso()),
    )


def create_item(
    conn: sqlite3.Connection,
    fields: dict,
    metrics: list[dict] = (),
    skills: list[str] = (),
    session_id: str | None = None,
    changed_by: str = "analyzer",
    reason: str | None = None,
) -> int:
    f = {k: fields.get(k) for k in ITEM_FIELDS}
    f["status"] = f["status"] or "draft"
    f["quantifiable"] = f["quantifiable"] or "maybe"
    f["source_turn_seqs"] = json.dumps(f["source_turn_seqs"] or [])
    ts = now_iso()
    cols = [*ITEM_FIELDS, "session_id", "version", "created_at", "updated_at"]
    vals = [f[k] for k in ITEM_FIELDS] + [session_id, 1, ts, ts]
    item_id = conn.execute(
        f"INSERT INTO memory_items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
    ).lastrowid
    for m in metrics:
        add_metric(conn, item_id, m)
    set_skills(conn, item_id, skills)
    _write_version(conn, item_id, 1, changed_by, reason)
    return item_id


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    fields: dict,
    changed_by: str,
    reason: str | None = None,
    metrics: list[dict] | None = None,
    metric_updates: list[dict] = (),
    add_metrics: list[dict] = (),
    skills: list[str] | None = None,
) -> int:
    """Apply changes and write a new version. `metrics` replaces all metrics; `metric_updates`
    changes existing ones by id; `add_metrics` appends. Returns the new version number."""
    sets, args = [], []
    for k, v in fields.items():
        if k not in EDITABLE and k not in ("source_quote", "source_turn_seqs", "merged_into"):
            raise ValueError(f"field {k} can't be edited")
        sets.append(f"{k} = ?")
        args.append(json.dumps(v) if k == "source_turn_seqs" else v)
    if metrics is not None:
        conn.execute("DELETE FROM metrics WHERE item_id = ?", (item_id,))
        for m in metrics:
            add_metric(conn, item_id, m)
    for mu in metric_updates:
        update_metric(conn, item_id, mu)
    for m in add_metrics:
        add_metric(conn, item_id, m)
    if skills is not None:
        set_skills(conn, item_id, skills)
    version = (
        conn.execute("SELECT version FROM memory_items WHERE id = ?", (item_id,)).fetchone()[
            "version"
        ]
        + 1
    )
    sets += ["version = ?", "updated_at = ?"]
    args += [version, now_iso()]
    conn.execute(f"UPDATE memory_items SET {', '.join(sets)} WHERE id = ?", [*args, item_id])
    _write_version(conn, item_id, version, changed_by, reason)
    return version


def restore_version(conn: sqlite3.Connection, item_id: int, version: int) -> int:
    r = conn.execute(
        "SELECT snapshot FROM memory_item_versions WHERE item_id = ? AND version = ?",
        (item_id, version),
    ).fetchone()
    if not r:
        raise KeyError(f"item {item_id} has no version {version}")
    snap = json.loads(r["snapshot"])
    fields = {k: snap[k] for k in EDITABLE if k in snap}
    return update_item(
        conn,
        item_id,
        fields,
        "restore",
        f"restored version {version}",
        metrics=snap.get("metrics", []),
        skills=snap.get("skills", []),
    )


def item_versions(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    out = rows(
        conn.execute(
            "SELECT version, changed_by, reason, created_at, snapshot FROM memory_item_versions"
            " WHERE item_id = ? ORDER BY version DESC",
            (item_id,),
        )
    )
    for v in out:
        v["snapshot"] = json.loads(v["snapshot"])
    return out


def set_skills(conn: sqlite3.Connection, item_id: int, skills: list[str]) -> None:
    conn.execute("DELETE FROM item_skills WHERE item_id = ?", (item_id,))
    for name in skills or []:
        if name and name.strip():
            conn.execute(
                "INSERT OR IGNORE INTO item_skills (item_id, skill_id) VALUES (?, ?)",
                (item_id, skill_id_for(conn, name)),
            )


def add_link(conn: sqlite3.Connection, from_id: int, to_id: int, kind: str) -> None:
    if from_id != to_id:
        conn.execute(
            "INSERT OR IGNORE INTO item_links (from_id, to_id, kind) VALUES (?, ?, ?)",
            (from_id, to_id, kind),
        )


# ------------------------------------------------------------------ metrics


def _metric_values(m: dict) -> dict:
    d = compute_deltas(
        m.get("baseline"),
        m.get("final"),
        m.get("delta_pct"),
        m.get("unit"),
        m.get("better_when") or "higher",
        m.get("source_text") or "",
    )
    delta_abs = d.delta_abs if d.delta_abs is not None else m.get("delta_abs")
    return {
        "name": (m.get("name") or "result")[:120],
        "category": m.get("category") or "other",
        "unit": m.get("unit") or "count",
        "baseline": d.baseline,
        "final": d.final,
        "delta_abs": delta_abs,
        "delta_pct": d.delta_pct,
        "better_when": m.get("better_when") or "higher",
        "period": m.get("period"),
        "approx": int(bool(m.get("approx"))),
        "is_derived": int(d.is_derived),
        "source_text": m.get("source_text") or "",
    }


def add_metric(conn: sqlite3.Connection, item_id: int, m: dict) -> int:
    v = _metric_values(m)
    return conn.execute(
        f"INSERT INTO metrics (item_id, {', '.join(METRIC_FIELDS)})"
        f" VALUES (?, {', '.join('?' * len(METRIC_FIELDS))})",
        [item_id, *(v[k] for k in METRIC_FIELDS)],
    ).lastrowid


def update_metric(conn: sqlite3.Connection, item_id: int, m: dict) -> None:
    cur = conn.execute(
        "SELECT * FROM metrics WHERE id = ? AND item_id = ?", (m["id"], item_id)
    ).fetchone()
    if not cur:
        raise KeyError(f"metric {m['id']} not on item {item_id}")
    merged = dict(cur)
    for k in ("name", "unit", "source_text", "better_when"):
        if m.get(k) is not None:
            merged[k] = m[k]
    if m.get("baseline") is not None or m.get("final") is not None:
        # new before/after values: deltas are recomputed from them
        for k in ("baseline", "final"):
            if m.get(k) is not None:
                merged[k] = m[k]
        merged["delta_pct"] = merged["delta_abs"] = None
    elif m.get("delta_pct") is not None:
        # a stated percentage replaces any derived values
        merged.update(baseline=None, final=None, delta_abs=None, delta_pct=m["delta_pct"])
    v = _metric_values(merged)
    conn.execute(
        f"UPDATE metrics SET {', '.join(f'{k} = ?' for k in METRIC_FIELDS)} WHERE id = ?",
        [*(v[k] for k in METRIC_FIELDS), m["id"]],
    )


# ------------------------------------------------------------------ pending questions


def _pending_dicts(conn, sql: str, args) -> list[dict]:
    return rows(
        conn.execute(
            "SELECT q.*, i.title AS item_title FROM pending_questions q"
            " LEFT JOIN memory_items i ON i.id = q.item_id " + sql,
            args,
        )
    )


def pending_for_item(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    return _pending_dicts(
        conn, "WHERE q.item_id = ? AND q.status = 'open' ORDER BY q.id", (item_id,)
    )


def get_pending(conn: sqlite3.Connection, pid: int) -> dict | None:
    out = _pending_dicts(conn, "WHERE q.id = ?", (pid,))
    return out[0] if out else None


def open_pending(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """Open questions, oldest first; with `today`, only those not snoozed past today."""
    if today:
        return _pending_dicts(
            conn,
            "WHERE q.status = 'open' AND (q.snooze_until IS NULL OR q.snooze_until <= ?)"
            " ORDER BY q.created_at, q.id",
            (today,),
        )
    return _pending_dicts(conn, "WHERE q.status = 'open' ORDER BY q.created_at, q.id", ())


def create_pending(conn: sqlite3.Connection, item_id: int | None, kind: str, question: str) -> int:
    dup = conn.execute(
        "SELECT id FROM pending_questions WHERE item_id IS ? AND kind = ? AND status = 'open'",
        (item_id, kind),
    ).fetchone()
    if dup:
        return dup["id"]
    return conn.execute(
        "INSERT INTO pending_questions (item_id, kind, question, created_at) VALUES (?, ?, ?, ?)",
        (item_id, kind, question[:300], now_iso()),
    ).lastrowid


def resolve_pending(
    conn: sqlite3.Connection, pid: int, status: str, answer_text: str | None = None
) -> None:
    conn.execute(
        "UPDATE pending_questions SET status = ?, answer_text = coalesce(?, answer_text),"
        " resolved_at = ? WHERE id = ?",
        (status, answer_text, now_iso(), pid),
    )


def snooze_pending(
    conn: sqlite3.Connection, pid: int, until: str, answer_text: str | None = None
) -> None:
    conn.execute(
        "UPDATE pending_questions SET snooze_until = ?, answer_text = coalesce(?, answer_text)"
        " WHERE id = ?",
        (until, answer_text, pid),
    )


def mark_asked(conn: sqlite3.Connection, pids: list[int], max_asks: int = 3) -> None:
    """Count an ask; a question asked `max_asks` times and still open expires (plan 7.2)."""
    ts = now_iso()
    for pid in pids:
        conn.execute(
            "UPDATE pending_questions SET times_asked = times_asked + 1, last_asked_at = ?"
            " WHERE id = ?",
            (ts, pid),
        )
        conn.execute(
            "UPDATE pending_questions SET status = 'expired', resolved_at = ?"
            " WHERE id = ? AND status = 'open' AND times_asked >= ?",
            (ts, pid, max_asks),
        )


# ------------------------------------------------------------------ digests


def upsert_digest(conn, kind: str, start: str, end: str, text: str, item_ids: list[int]) -> int:
    conn.execute(
        "INSERT INTO digests (kind, period_start, period_end, text, item_ids, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, period_start) DO UPDATE SET"
        " period_end = excluded.period_end, text = excluded.text, item_ids = excluded.item_ids",
        (kind, start, end, text, json.dumps(item_ids), now_iso()),
    )
    return conn.execute(
        "SELECT id FROM digests WHERE kind = ? AND period_start = ?", (kind, start)
    ).fetchone()["id"]


# ------------------------------------------------------------------ search documents


def item_doc_text(item: dict) -> str:
    """Plan 8.4's fixed template."""
    metrics = "; ".join(f"{m['name']} {m['display']}" for m in item.get("metrics", []))
    project = item["project"]["name"] if item.get("project") else "no project"
    parts = [
        f"{item['occurred_on']} · {project} · {item['kind']}: {item['title']}.",
        f"{item['what']}.",
    ]
    if item.get("how"):
        parts.append(f"How: {item['how']}.")
    if item.get("result"):
        parts.append(f"Result: {item['result']}.")
    if metrics:
        parts.append(f"Metrics: {metrics}.")
    if item.get("skills"):
        parts.append(f"Skills: {', '.join(item['skills'])}.")
    if item.get("bullet"):
        parts.append(item["bullet"])
    return " ".join(parts)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def docs_needing_update(
    conn: sqlite3.Connection, item_ids: list[int] | None = None
) -> tuple[list[dict], list[int]]:
    """Read-only. Returns (docs to (re)embed, item ids whose doc must be dropped because the item
    is hidden or merged)."""
    out, drop = [], []
    items = (
        list_items(conn, ids=item_ids, include_hidden=True)
        if item_ids is not None
        else list_items(conn, include_hidden=True, limit=100_000)
    )
    for it in items:
        if it["status"] in ("hidden", "merged"):
            if conn.execute(
                "SELECT 1 FROM docs WHERE doc_type = 'item' AND ref_id = ?", (it["id"],)
            ).fetchone():
                drop.append(it["id"])
            continue
        text = item_doc_text(it)
        h = content_hash(text)
        cur = conn.execute(
            "SELECT content_hash, embedding FROM docs WHERE doc_type = 'item' AND ref_id = ?",
            (it["id"],),
        ).fetchone()
        if not cur or cur["content_hash"] != h or cur["embedding"] is None:
            out.append(
                {
                    "doc_type": "item",
                    "ref_id": it["id"],
                    "text": text,
                    "occurred_on": it["occurred_on"],
                    "project_id": it["project"]["id"] if it["project"] else None,
                }
            )
    if item_ids is None:
        for d in rows(conn.execute("SELECT * FROM digests")):
            text = f"{d['period_start']} · {d['kind']} summary: {d['text']}"
            h = content_hash(text)
            cur = conn.execute(
                "SELECT content_hash, embedding FROM docs WHERE doc_type = 'digest' AND ref_id = ?",
                (d["id"],),
            ).fetchone()
            if not cur or cur["content_hash"] != h or cur["embedding"] is None:
                out.append(
                    {
                        "doc_type": "digest",
                        "ref_id": d["id"],
                        "text": text,
                        "occurred_on": d["period_start"],
                        "project_id": None,
                    }
                )
    return out, drop


def upsert_doc(
    conn: sqlite3.Connection, doc: dict, embedding: bytes | None, embed_model: str
) -> int:
    conn.execute(
        "INSERT INTO docs (doc_type, ref_id, text, occurred_on, project_id, embedding, embed_model,"
        " content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(doc_type, ref_id) DO UPDATE SET"
        " text = excluded.text, occurred_on = excluded.occurred_on, project_id ="
        " excluded.project_id, embedding = excluded.embedding, embed_model = excluded.embed_model,"
        " content_hash = excluded.content_hash",
        (
            doc["doc_type"],
            doc["ref_id"],
            doc["text"],
            doc["occurred_on"],
            doc["project_id"],
            embedding,
            embed_model,
            content_hash(doc["text"]),
        ),
    )
    return conn.execute(
        "SELECT id FROM docs WHERE doc_type = ? AND ref_id = ?", (doc["doc_type"], doc["ref_id"])
    ).fetchone()["id"]
