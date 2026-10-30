"""Admin (owner only, plan 13.5): overview KPIs, sessions, per-session traces, eval runs."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from daybook.agents.judge import flagged
from daybook.api.deps import ApiError, app_dep, now_dep, owner_only
from daybook.engine.app import App, User
from daybook.memory import repo
from daybook.settings import REPO_ROOT

router = APIRouter(prefix="/admin")


def _sessions(app: App, user: User, since: str | None = None) -> list[dict]:
    with app.vault(user).read() as conn:
        rows = repo.rows(
            conn.execute(
                "SELECT * FROM sessions WHERE (? IS NULL OR local_date >= ?) ORDER BY started_at DESC",
                (since, since),
            )
        )
    out = []
    for r in rows:
        stats = json.loads(r["stats"] or "{}")
        if stats.get("seed"):
            continue
        judge = stats.get("judge")
        out.append(
            {
                "id": r["id"],
                "mode": r["mode"],
                "local_date": r["local_date"],
                "started_at": r["started_at"],
                "duration_sec": stats.get("duration_sec"),
                "turn_count": r["turn_count"],
                "status": r["status"],
                "end_reason": r["end_reason"],
                "cost_usd": stats.get("cost_usd"),
                "latency_p50_ms": stats.get("latency_p50_ms"),
                "guard_fails": sum((stats.get("guard_fails") or {}).values()),
                "judge": judge,
                "flagged": bool(judge and flagged(judge)),
                "feedback": r["feedback"],
                "_stats": stats,
            }
        )
    return out


def _pct(xs: list[float], p: float) -> int | None:
    if not xs:
        return None
    xs = sorted(xs)
    return int(xs[min(len(xs) - 1, int(p * len(xs)))])


@router.get("/overview")
def overview(
    days: int = 30,
    app: App = Depends(app_dep),
    user: User = Depends(owner_only),
    now: datetime = Depends(now_dep),
):
    since = (now - timedelta(days=days)).date().isoformat()
    ss = _sessions(app, user, since)
    done = [s for s in ss if s["status"] == "finalized"]
    lat = [s["_stats"].get("latency_p50_ms") for s in done if s["_stats"].get("latency_p50_ms")]
    lat95 = [s["_stats"].get("latency_p95_ms") for s in done if s["_stats"].get("latency_p95_ms")]
    spans: dict[str, list[int]] = {}
    for key in app.store.list(f"{user.prefix}traces/")[-400:]:
        if "/jobs/" in key:
            continue
        b = app.store_for(user).get(key)
        if not b:
            continue
        for sp in json.loads(b.data).get("spans", []):
            if sp["kind"] != "root":
                spans.setdefault(sp["name"], []).append(sp["latency_ms"])
    guard_totals: dict[str, int] = {}
    for s in done:
        for k, v in (s["_stats"].get("guard_fails") or {}).items():
            guard_totals[k] = guard_totals.get(k, 0) + v
    n_replies = sum(s["turn_count"] or 0 for s in done) or 1
    judges = [s["judge"] for s in done if s["judge"]]
    with app.vault(user).read() as conn:
        q_items = conn.execute(
            "SELECT count(*), sum(EXISTS(SELECT 1 FROM metrics m WHERE m.item_id = i.id))"
            " FROM memory_items i WHERE quantifiable = 'yes' AND occurred_on >= ?"
            " AND session_id NOT LIKE 'seed%'",
            (since,),
        ).fetchone()
        pend = conn.execute(
            "SELECT count(*), sum(status IN ('answered','not_quantifiable')) FROM pending_questions"
            " WHERE created_at >= ?",
            (since,),
        ).fetchone()
    asks = [
        json.loads(app.store_for(user).get(k).data)
        for k in app.store.list(f"{user.prefix}traces/")
        if "/jobs/ask-" in k
    ][-200:]
    return {
        "days": days,
        "sessions": len(ss),
        "completed": len(done),
        "completion_rate": round(len(done) / len(ss), 3) if ss else None,
        "median_duration_sec": statistics.median([s["duration_sec"] or 0 for s in done])
        if done
        else None,
        "time_limit_hits": sum(1 for s in ss if s["end_reason"] == "time_limit"),
        "cost_total_usd": round(sum(s["cost_usd"] or 0 for s in ss), 4),
        "cost_per_session_usd": round(sum(s["cost_usd"] or 0 for s in done) / len(done), 4)
        if done
        else None,
        "latency": {
            "turn_p50_ms": int(statistics.median(lat)) if lat else None,
            "turn_p95_ms": max(lat95) if lat95 else None,
            "by_span": {
                k: {"p50_ms": _pct(v, 0.5), "p95_ms": _pct(v, 0.95)}
                for k, v in sorted(spans.items())
            },
        },
        "tts_chars": sum(s["_stats"].get("tts_chars", 0) for s in ss),
        "goal_adherence_avg": round(statistics.mean(j["goal_adherence"] for j in judges), 3)
        if judges
        else None,
        "guard_fail_rate": {k: round(v / n_replies, 3) for k, v in guard_totals.items()},
        "fidelity_issues": sum(j.get("fidelity_issues", 0) for j in judges),
        "metric_capture_rate": round((q_items[1] or 0) / q_items[0], 3) if q_items[0] else None,
        "pending_resolution_rate": round((pend[1] or 0) / pend[0], 3) if pend[0] else None,
        "ask_abstain_rate": round(sum(1 for a in asks if a.get("abstained")) / len(asks), 3)
        if asks
        else None,
        "feedback": {
            "up": sum(1 for s in ss if s["feedback"] == "up"),
            "down": sum(1 for s in ss if s["feedback"] == "down"),
        },
        "spend_today": app.spend.today(now),
    }


@router.get("/sessions")
def sessions(app: App = Depends(app_dep), user: User = Depends(owner_only)):
    return {
        "sessions": [{k: v for k, v in s.items() if k != "_stats"} for s in _sessions(app, user)]
    }


@router.get("/sessions/{sid}/trace")
def trace(sid: str, app: App = Depends(app_dep), user: User = Depends(owner_only)):
    row = next((s for s in _sessions(app, user) if s["id"] == sid), None)
    keys = [k for k in app.store.list(f"{user.prefix}traces/") if f"/{sid}/" in k or sid in k]
    if row is None and not keys:
        raise ApiError(404, "not_found", "No such session.")
    turns, jobs = [], []
    for k in sorted(keys):
        b = app.store_for(user).get(k)
        if b:
            (jobs if "/jobs/" in k else turns).append(json.loads(b.data))
    return {
        "session": {k: v for k, v in (row or {"id": sid}).items() if k != "_stats"},
        "turns": turns,
        "jobs": jobs,
    }


@router.get("/evals")
def evals(user: User = Depends(owner_only)):
    runs = []
    d = REPO_ROOT / "evals" / "results"
    for f in sorted(d.glob("*.json"), reverse=True)[:20] if d.exists() else []:
        try:
            r = json.loads(f.read_text())
            runs.append(
                {
                    "id": f.stem,
                    "created_at": r.get("created_at"),
                    "mode": r.get("mode"),
                    "suites": r.get("suites", {}),
                }
            )
        except Exception:
            continue
    return {"runs": runs}
