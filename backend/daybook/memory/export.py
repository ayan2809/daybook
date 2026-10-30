"""Exports (plan 8.5): Resume-as-code notes (its Note contract), a Markdown digest, and the brain
export: the whole memory as one Markdown file for an LLM (DB-33)."""

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


KIND_HEADINGS = [
    ("achievement", "Achievements"),
    ("progress", "Progress"),
    ("soft_skill", "Soft skills shown"),
    ("learning", "Things learned"),
    ("decision", "Decisions"),
    ("challenge", "Challenges"),
    ("blocker", "Blockers"),
    ("praise", "Praise received"),
    ("plan", "Plans"),
]

BRAIN_PREAMBLE = """> **For the AI reading this:** this is {who}'s work memory, exported from Daybook, a voice
> journal they talk to for a few minutes each evening. Every entry comes from their own words. The
> numbers were checked against what they said and the deltas were computed in code, so you can
> rely on them; don't invent new ones. "You said" lines are verbatim quotes. Items marked
> *(draft)* weren't confirmed yet, and "Open questions" lists what's still unknown. Use this as
> context for resume bullets, appraisal notes, status updates or interview stories. When something
> isn't here, say so instead of guessing."""


def _month(d: str) -> str:
    from datetime import date as _date

    return _date.fromisoformat(d[:10]).strftime("%B %Y")


def brain(conn: sqlite3.Connection, now=None) -> str:
    """The whole memory as one Markdown file for an LLM's context window (DB-33): a note to the
    model, the profile, items by kind and month with metrics and quotes, skills, projects, open
    questions and the recent day summaries."""
    profile = repo.get_profile(conn) or {}
    name = profile.get("display_name")
    who = name or "the user"
    items = list(reversed(repo.list_items(conn, limit=100_000)))
    title = f"# {name}'s work memory (Daybook export)" if name else "# Work memory (Daybook export)"
    lines = [title, "", BRAIN_PREAMBLE.format(who=who), ""]

    facts = [f"- Name: {name}"] if name else []
    if profile.get("role_title"):
        facts.append(f"- Role: {profile['role_title']}")
    if profile.get("org"):
        facts.append(f"- Organisation: {profile['org']}")
    if items:
        facts.append(
            f"- Covers: {items[0]['occurred_on']} to {items[-1]['occurred_on']}"
            f" ({len(items)} entries)"
        )
    if now is not None:
        facts.append(f"- Exported: {now.date().isoformat()}")
    lines += ["## Profile", "", *facts, ""]

    for kind, heading in KIND_HEADINGS:
        group = [it for it in items if it["kind"] == kind]
        if not group:
            continue
        lines += [f"## {heading}", ""]
        month = None
        for it in group:
            m = _month(it["occurred_on"])
            if m != month:
                month = m
                lines += [f"### {m}", ""]
            proj = f" · {it['project']['name']}" if it["project"] else ""
            draft = " *(draft)*" if it["status"] == "draft" else ""
            lines.append(f"- **{it['title']}** ({it['occurred_on']}{proj}){draft}")
            if it["bullet"]:
                lines.append(f"  - Resume bullet: {it['bullet']}")
            for met in it["metrics"]:
                lines.append(f"  - {met['name']}: {met['display']}")
            if it["skills"]:
                lines.append(f"  - Skills: {', '.join(it['skills'])}")
            for p in it["open_pending"]:
                lines.append(f"  - Still open: {p['question']}")
            lines.append(f'  - You said: "{it["source_quote"]}"')
        lines.append("")

    skills = conn.execute(
        "SELECT s.name, s.category, count(*) AS n FROM item_skills i JOIN skills s"
        " ON s.id = i.skill_id GROUP BY s.id ORDER BY n DESC, s.name"
    ).fetchall()
    if skills:
        lines += ["## Skills (times used)", ""]
        lines += [f"- {r['name']} ({r['category']}): {r['n']}" for r in skills]
        lines.append("")

    projects = repo.list_projects(conn)
    if projects:
        lines += ["## Projects", ""]
        for p in projects:
            summary = f": {p['summary']}" if p.get("summary") else ""
            lines.append(f"- {p['name']} ({p['kind']}, {p['status']}){summary}")
        lines.append("")

    pending = repo.open_pending(conn)
    if pending:
        lines += ["## Open questions (not answered yet)", ""]
        for p in pending:
            about = f" (about: {p['item_title']})" if p.get("item_title") else ""
            lines.append(f"- {p['question']}{about}")
        lines.append("")

    days = conn.execute(
        "SELECT period_start, text FROM digests WHERE kind = 'day'"
        " ORDER BY period_start DESC LIMIT 14"
    ).fetchall()
    if days:
        lines += ["## Recent days, in their own summary", ""]
        lines += [f"- **{r['period_start']}**: {r['text']}" for r in reversed(days)]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
