"""Exports (plan 8.5): Resume-as-code notes (its Note contract) and a Markdown digest."""

from __future__ import annotations

import sqlite3

from daybook.memory import repo

SKILL_CATEGORY = {"soft": "practice"}
EXPORTED_KINDS = ("achievement", "soft_skill")


def _session_raw(conn: sqlite3.Connection, item: dict) -> str:
    seqs = item.get("source_turn_seqs") or []
    sid = conn.execute("SELECT session_id FROM memory_items WHERE id = ?", (item["id"],)).fetchone()
    if sid and sid[0] and seqs:
        rows = conn.execute(
            f"SELECT text FROM turns WHERE session_id = ? AND role = 'user'"
            f" AND seq IN ({','.join('?' * len(seqs))}) ORDER BY seq",
            (sid[0], *seqs),
        ).fetchall()
        if rows:
            return " ".join(r[0] for r in rows)
    return item["source_quote"]


def resume_as_code(conn: sqlite3.Connection, since: str | None = None) -> list[dict]:
    """One note per exported item. Achievements and soft skills become `achievements[]`; learning
    items become `skills[]` with proficiency "learning"; other kinds stay in Daybook (recall only)."""
    profile = repo.get_profile(conn) or {}
    notes = []
    for it in reversed(repo.list_items(conn, date_from=since, limit=100_000)):
        if it["kind"] == "learning":
            if it["skills"]:
                notes.append(
                    {
                        "raw": _session_raw(conn, it),
                        "intents": ["skill"],
                        "skills": [{"name": s, "proficiency": "learning"} for s in it["skills"]],
                    }
                )
            continue
        if it["kind"] not in EXPORTED_KINDS or it["status"] not in ("active", "draft"):
            continue
        skill_rows = conn.execute(
            "SELECT s.name, s.category FROM item_skills i JOIN skills s ON s.id = i.skill_id"
            " WHERE i.item_id = ?",
            (it["id"],),
        ).fetchall()
        ach = {
            "org": profile.get("org"),
            "title": profile.get("role_title"),
            "occurred_on": it["occurred_on"][:7],
            "date_is_estimate": False,
            "x_accomplished": it["result"] or it["title"],
            "y_measure": "; ".join(m["display"] for m in it["metrics"]) or None,
            "z_action": it["how"] or it["what"],
            "bullet": it["bullet"] or it["title"],
            "scope": it["scope"],
            "impact": it["impact"] or 2,
            "status": "active" if it["status"] == "active" else "draft",
            "open_questions": [p["question"] for p in it["open_pending"]],
            "skills": [r[0] for r in skill_rows],
            "metrics": [
                {
                    k: m[k]
                    for k in (
                        "name",
                        "category",
                        "unit",
                        "baseline",
                        "final",
                        "delta_abs",
                        "delta_pct",
                        "better_when",
                        "period",
                        "is_derived",
                        "source_text",
                    )
                }
                for m in it["metrics"]
            ],
        }
        if it["project"]:
            ach["project"] = it["project"]["name"]
        notes.append(
            {
                "raw": _session_raw(conn, it),
                "intents": ["work_achievement"],
                "skills": [
                    {"name": r[0], "category": SKILL_CATEGORY.get(r[1], r[1])} for r in skill_rows
                ],
                "achievements": [{k: v for k, v in ach.items() if v is not None}],
            }
        )
    return notes


def markdown(conn: sqlite3.Connection) -> str:
    lines = ["# Daybook export", ""]
    current = None
    for it in repo.list_items(conn, limit=100_000):
        if it["occurred_on"] != current:
            current = it["occurred_on"]
            lines += ["", f"## {current}", ""]
        proj = f" · {it['project']['name']}" if it["project"] else ""
        lines.append(f"- **{it['title']}** ({it['kind']}{proj}, {it['status']})")
        if it["bullet"]:
            lines.append(f"  - {it['bullet']}")
        for m in it["metrics"]:
            lines.append(f"  - {m['name']}: {m['display']}")
        lines.append(f'  - You said: "{it["source_quote"]}"')
    return "\n".join(lines) + "\n"
