"""Demo mode for judges (plan 15): a sandbox seeded with Meera, dates shifted so her last
reflection is always "yesterday", caps, suggested replies, and replay mode.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daybook.engine.app import App, User, demo_user
from daybook.memory import db, repo, retrieval
from daybook.settings import REPO_ROOT

SEED_DB = REPO_ROOT / "data" / "seed" / "demo-memory.db"
SEED_JSON = REPO_ROOT / "data" / "seed" / "meera.json"
REPLAY_JSON = REPO_ROOT / "data" / "seed" / "replay.json"
_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")

SUGGESTIONS = {
    "close_loops": [
        "It went from about 40 alerts a week to around 6.",
        "Not sure yet, I'll check tomorrow.",
        "I wrote the migration plan and did most of the reviews.",
    ],
    "explore": [
        "I finished the load test: the retry service held up to 450 requests per second.",
        "Also did some stuff on the ledger thing.",
        "Spent an hour pairing with the intern on her first on-call shift.",
        "Actually, that migration last Tuesday was 40% faster, not 50.",
    ],
    "wrap": ["No, that's everything.", "Let's wrap up."],
    "edit": ["It was 40%, not 50%.", "Yes, that one.", "Yes."],
}


def suggestions_for(phase: str, pending_action: dict | None) -> list[str]:
    if pending_action:
        if pending_action["kind"] in ("confirm_edit", "confirm_metric", "confirm_item"):
            return ["Yes.", "No."]
        return SUGGESTIONS["edit"][:1]
    return SUGGESTIONS.get(
        {"followups": "explore", "dump": "explore", "open": "explore"}.get(phase, phase), []
    )


def _shift_dates(path: Path, today: date) -> None:
    conn = db.connect(path)
    try:
        last = conn.execute("SELECT max(local_date) FROM sessions").fetchone()[0]
        if not last:
            return
        offset = (today - timedelta(days=1) - date.fromisoformat(last)).days
        # keep weekdays aligned when possible: the seed's last day is a Friday
        if offset == 0:
            return
        mapping = {}

        def shift(d: str | None) -> str | None:
            if not d:
                return d
            if d not in mapping:
                mapping[d] = (date.fromisoformat(d[:10]) + timedelta(days=offset)).isoformat()
            return mapping[d]

        conn.execute("BEGIN")
        for table, cols in (
            ("memory_items", ("occurred_on",)),
            ("sessions", ("local_date",)),
            ("digests", ("period_start", "period_end")),
            ("docs", ("occurred_on",)),
            ("pending_questions", ("snooze_until",)),
        ):
            for c in cols:
                # newest first when moving forward (oldest first backward), so a UNIQUE date
                # column never collides with a row that hasn't moved yet
                order = "DESC" if offset > 0 else "ASC"
                for row in conn.execute(
                    f"SELECT rowid, {c} FROM {table} ORDER BY {c} {order}"
                ).fetchall():
                    conn.execute(
                        f"UPDATE {table} SET {c} = ? WHERE rowid = ?", (shift(row[1]), row[0])
                    )
        for row in conn.execute("SELECT id, text FROM docs").fetchall():
            text = _DATE.sub(lambda m: shift(m.group(0)), row["text"])
            conn.execute(
                "UPDATE docs SET text = ?, content_hash = ? WHERE id = ?",
                (text, repo.content_hash(text), row["id"]),
            )
        for row in conn.execute("SELECT id, snapshot FROM memory_item_versions").fetchall():
            snap = json.loads(row["snapshot"])
            snap["occurred_on"] = shift(snap.get("occurred_on"))
            conn.execute(
                "UPDATE memory_item_versions SET snapshot = ? WHERE id = ?",
                (json.dumps(snap), row["id"]),
            )
        conn.execute("COMMIT")
    finally:
        db.close(conn)


async def create_sandbox(app: App, now: datetime) -> User:
    if not SEED_DB.exists():
        raise RuntimeError("data/seed/demo-memory.db is missing: run `make seed`")
    sandbox = uuid.uuid4().hex[:12]
    user = demo_user(sandbox)
    work = Path(tempfile.mkdtemp()) / "memory.db"
    shutil.copyfile(SEED_DB, work)
    _shift_dates(work, now.astimezone(ZoneInfo("Asia/Kolkata")).date())
    stale = _clear_foreign_embeddings(work, app.embedder.model_name)
    app.store.put(user.vault_key, work.read_bytes(), if_none_match=True)
    shutil.rmtree(work.parent, ignore_errors=True)
    if stale:  # the seed was embedded with another model: re-embed for this app's embedder
        await retrieval.index(app.vault(user), app.embedder)
    return user


def _clear_foreign_embeddings(path: Path, model: str) -> int:
    conn = db.connect(path)
    try:
        n = conn.execute(
            "UPDATE docs SET embedding = NULL WHERE embed_model IS NOT ? OR embedding IS NULL",
            (model,),
        ).rowcount
        if n and db.vec_enabled(conn):
            conn.execute("DELETE FROM docs_vec")
        return n
    finally:
        db.close(conn)


def replay_payload() -> dict:
    if REPLAY_JSON.exists():
        return json.loads(REPLAY_JSON.read_text())
    return {
        "banner": "Replay: the demo's daily limit was reached, so this is a recorded session.",
        "turns": [],
        "summary": None,
    }


def build_seed_db(path: Path, embed: bool = False) -> sqlite3.Connection:
    """Build the demo vault from meera.json (no LLM). Embeddings are added by `index()` later."""
    data = json.loads(SEED_JSON.read_text())
    path.unlink(missing_ok=True)
    conn = db.connect(path)
    conn.execute("BEGIN")
    p = data["profile"]
    repo.upsert_profile(conn, **p)
    repo.put_setting(conn, "app_settings", {"keep_names": False, "voice": True})
    for proj in data["projects"]:
        conn.execute(
            "INSERT INTO projects (name, aliases, kind, summary, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                proj["name"],
                json.dumps(proj.get("aliases", [])),
                proj.get("kind", "work"),
                proj.get("summary"),
                repo.now_iso(),
            ),
        )
    keys: dict[str, int] = {}
    for n, day in enumerate(data["days"]):
        sid = f"seed{n:02d}"
        d = day["date"]
        started = f"{d}T13:35:00+00:00"
        user_turns = [t for t in day["turns"] if t["role"] == "user"]
        repo.upsert_session(
            conn,
            {
                "id": sid,
                "mode": day.get("mode", "guided")
                if day.get("mode") in ("guided", "dump", "quick")
                else "guided",
                "local_date": d,
                "started_at": started,
                "ended_at": f"{d}T13:40:30+00:00",
                "status": "finalized",
                "end_reason": "user",
                "turn_count": len(user_turns),
                "energy": day.get("energy"),
                "mood_word": day.get("mood_word"),
                "stats": {
                    "duration_sec": 330,
                    "turns": len(user_turns),
                    "spoken_pct": 95,
                    "cost_usd": 0.0,
                    "seed": True,
                },
            },
        )
        repo.insert_turns(
            conn,
            sid,
            [
                {"seq": i + 1, "role": t["role"], "text": t["text"], "created_at": started}
                for i, t in enumerate(day["turns"])
            ],
        )
        ids = []
        for it in day["items"]:
            fields = {
                k: it.get(k)
                for k in (
                    "kind",
                    "status",
                    "title",
                    "what",
                    "how",
                    "result",
                    "bullet",
                    "ownership",
                    "quantifiable",
                    "scope",
                    "impact",
                    "source_quote",
                )
            }
            fields["occurred_on"] = d
            fields["project_id"] = repo.project_id_for(conn, it.get("project"), create=True)
            iid = repo.create_item(
                conn,
                fields,
                it.get("metrics", []),
                it.get("skills", []),
                session_id=sid,
                reason="demo seed",
            )
            keys[it["key"]] = iid
            ids.append(iid)
            for link in it.get("links", []):
                if link["to"] in keys:
                    repo.add_link(conn, iid, keys[link["to"]], link["kind"])
        for pq in day.get("pending", []):
            pid = repo.create_pending(conn, keys.get(pq["item"]), pq["kind"], pq["question"])
            conn.execute(
                "UPDATE pending_questions SET created_at = ?, times_asked = ? WHERE id = ?",
                (started, pq.get("times_asked", 0), pid),
            )
            if pq.get("status", "open") != "open":
                repo.resolve_pending(conn, pid, pq["status"], pq.get("answer_text"))
        repo.upsert_digest(conn, "day", d, d, day["day_summary"], ids)
    conn.execute("COMMIT")
    return conn
