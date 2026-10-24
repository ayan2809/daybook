"""Offline evaluation (plan 13.4).

    uv run --project backend python -m evals run --suite all --mode fake
    uv run --project backend python -m evals run --suite router --mode live --yes

Fake mode runs the whole pipeline with the heuristic fake LLM (it checks plumbing, not quality) and
costs nothing. Live mode prints a cost estimate, refuses above $2, and needs --yes. Results go to
evals/results/<timestamp>-<sha>.json plus a short report.md; the Admin page lists them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "data"
RESULTS = ROOT / "evals" / "results"
SUITES = ["guards", "router", "extraction", "retrieval", "ask"]
# rough live cost per case (USD), from token counts seen in dev (obs/pricing.yaml prices)
COST_PER_CASE = {"router": 0.0004, "extraction": 0.006, "retrieval": 0.0001, "ask": 0.004,
                 "guards": 0.0}
GATES = {"router": ("accuracy", 0.90), "extraction": ("f1", 0.80),
         "retrieval": ("recall_at_3", 0.90), "ask": ("abstain_recall", 0.90), "guards": ("pass_rate", 1.0)}


def _jsonl(name: str) -> list[dict]:
    return [json.loads(ln) for ln in (DATA / f"{name}.jsonl").read_text().splitlines() if ln.strip()]


def _app(mode: str, tmp: Path):
    os.environ["DAYBOOK_LLM"] = "live" if mode == "live" else "fake"
    os.environ["DAYBOOK_EMBED"] = "gemini" if mode == "live" else "fake"
    os.environ["DAYBOOK_TTS"] = "off"
    os.environ["DAYBOOK_DATA_DIR"] = str(tmp)
    from daybook.engine.app import App
    from daybook.settings import Settings
    from daybook.storage.blob import LocalBlobStore

    return App(settings=Settings(), store=LocalBlobStore(tmp / "blobs"), tts=None)


# ------------------------------------------------------------------ suites


async def suite_guards(app) -> dict:
    from daybook.engine.guards import GuardContext, check, failures

    src = GuardContext(sources=["I shipped the retry worker, failures went from 1,200 to 100."])
    cases = [
        ("Nice work! What changed? And who helped?", "one_question"),
        (" ".join(["word"] * 60) + "?", "length"),
        ("You should add alerts. What else?", "no_advice"),
        ("So failures dropped 35%?", "fidelity"),
        ("So failures went from 1,200 to 100?", None),
        ("That sounds like a big one. How did you build it?", None),
    ]
    rows = []
    for text, want in cases:
        failed = failures(check(text, src))
        ok = (want in failed) if want else not failed
        rows.append({"text": text, "expected_fail": want, "failed": failed, "ok": ok})
    wrap = GuardContext(sources=[], wrap=True, promised=["the numbers for the retry worker"],
                        max_words=80)
    for text, ok_expected in [("Here's what I captured: the retry worker. Did I miss anything?", False),
                              ("I captured the retry worker. Tomorrow I'll ask about the numbers "
                               "for the retry worker. Did I miss anything?", True)]:
        failed = failures(check(text, wrap))
        rows.append({"text": text, "expected_fail": None if ok_expected else "phase",
                     "failed": failed, "ok": (not failed) == ok_expected})
    n_ok = sum(r["ok"] for r in rows)
    return {"pass_rate": round(n_ok / len(rows), 3), "n": len(rows),
            "failures": [r for r in rows if not r["ok"]]}


async def suite_router(app) -> dict:
    from daybook.agents.researcher import label_turn
    from daybook.obs.tracer import Trace

    rows, confusion = [], {}
    trace = Trace("eval")
    for case in _jsonl("router"):
        ctx = case["context"]
        label = await label_turn(
            app.deps, trace, case["text"], date(2026, 10, 24), "Asia/Kolkata", [],
            {"id": 1, "question": ctx["open_pending"]} if ctx.get("open_pending") else None,
            ctx.get("last_agent"), "the agent's read-back" if ctx.get("awaiting_confirmation") else None,
        )
        exp = case["expected"]
        ok = label.primary_intent == exp["primary_intent"] and (
            exp["primary_intent"] != "meta" or label.meta_command == exp["meta_command"])
        key = f"{exp['primary_intent']}→{label.primary_intent}"
        confusion[key] = confusion.get(key, 0) + 1
        rows.append({"id": case["id"], "ok": ok, "want": exp["primary_intent"],
                     "got": label.primary_intent, "inj_want": exp["injection_suspected"],
                     "inj_got": label.injection_suspected})
    inj = [r for r in rows if r["inj_want"]]
    return {
        "accuracy": round(sum(r["ok"] for r in rows) / len(rows), 3), "n": len(rows),
        "injection_recall": round(sum(r["inj_got"] for r in inj) / len(inj), 3) if inj else None,
        "confusion": {k: v for k, v in sorted(confusion.items()) if k.split("→")[0] != k.split("→")[1]},
        "failures": [r for r in rows if not r["ok"]][:20], "cost_usd": round(trace.cost_usd, 4),
    }


def _match(exp: dict, got: dict) -> bool:
    text = f"{got['fields']['title']} {got['fields']['what']}".lower()
    kw = exp.get("title_keywords") or []
    kinds_ok = exp["kind"] == got["fields"]["kind"] or {exp["kind"], got["fields"]["kind"]} <= {
        "achievement", "progress"}
    return kinds_ok and (not kw or any(k.lower() in text for k in kw))


async def suite_extraction(app) -> dict:
    from daybook.agents.analyzer_fast import fast_pass
    from daybook.engine.conductor import _merge_delta
    from daybook.engine.finalize import _deep_pass, _validate
    from daybook.engine.state import SessionState, TurnRec
    from daybook.memory.validate import number_supported
    from daybook.obs.tracer import Trace

    tp = fp = fn = 0
    invented = metrics_total = 0
    soft_with_metrics = 0
    rows = []
    trace = Trace("eval")
    for case in _jsonl("extraction"):
        st = SessionState(id=f"ev-{case['id']}", uid="owner", role="owner", mode="guided",
                          local_date="2026-10-24", tz="Asia/Kolkata",
                          started_at="2026-10-24T19:00:00+05:30", limit_sec=420)
        for i, t in enumerate(case["turns"]):
            st.turns.append(TurnRec(seq=i + 1, role=t["role"], text=t["text"],
                                    created_at="2026-10-24T13:30:00+00:00"))
            if t["role"] == "user":
                delta = await fast_pass(app.deps, trace, st.turn_dicts(),
                                        {k: v for k, v in st.board_items.items()},
                                        date(2026, 10, 24), "guided")
                if delta:
                    _merge_delta(st, delta)
        result = await _deep_pass(app, _owner(), st, trace)
        users = st.user_texts()
        for it in result.items:  # raw LLM output: did it invent numbers?
            for m in it.metrics:
                metrics_total += 1
                if not all(number_supported(getattr(m, f), users) for f in ("baseline", "final", "delta_pct")):
                    invented += 1
        plan = _validate(st, result)
        got = plan["items"]
        soft_with_metrics += sum(1 for g in got if g["fields"]["kind"] == "soft_skill" and g["metrics"])
        used = set()
        for exp in case["expected_items"]:
            j = next((j for j, g in enumerate(got) if j not in used and _match(exp, g)), None)
            if j is None:
                fn += 1
                rows.append({"id": case["id"], "missing": exp})
            else:
                used.add(j)
                tp += 1
        fp += len(got) - len(used)
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    return {"f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "precision": round(prec, 3), "recall": round(rec, 3),
            "invented_number_rate": round(invented / metrics_total, 3) if metrics_total else 0.0,
            "soft_skill_items_with_metrics": soft_with_metrics,
            "missing": rows[:20], "cost_usd": round(trace.cost_usd, 4)}


def _owner():
    from daybook.engine.app import OWNER

    return OWNER


async def _seed_vault(app):
    """The Meera vault (dates as in meera.json, so `today` = 2026-10-24 matches), embedded with the
    app's embedder. Returns (vault, key → item id)."""
    from daybook.engine.demo import build_seed_db
    from daybook.memory import db, retrieval

    path = Path(tempfile.mkdtemp()) / "seed.db"
    conn, keys = build_seed_db(path)
    db.close(conn)
    app.store.put(_owner().vault_key, path.read_bytes())
    vault = app.vault(_owner())
    await retrieval.index(vault, app.embedder)
    return vault, keys


async def suite_retrieval(app) -> dict:
    from daybook.agents.context import find
    from daybook.agents.researcher import label_turn
    from daybook.obs.tracer import Trace

    vault, keys = await _seed_vault(app)
    with vault.read() as conn:
        from daybook.memory import repo
        projects = repo.list_projects(conn)
    hits3 = mrr = 0.0
    rows = []
    trace = Trace("eval")
    for case in _jsonl("retrieval"):
        today = date.fromisoformat(case["today"])
        label = await label_turn(app.deps, trace, case["query"], today, "Asia/Kolkata", projects,
                                 None, None, None)
        res = await find(app.deps, vault, app.embedder, label.entities.target_hint or case["query"],
                         label.entities.time_refs, trace, abstain_cosine=-1.0, verify=False)
        # rank by the search itself (find() keeps the top 3)
        got = [c.item_id for c in res.candidates]
        want = {keys[k] for k in case["expected_keys"] if k in keys}
        hit = any(g in want for g in got[:3])
        rank = next((i + 1 for i, g in enumerate(got) if g in want), None)
        hits3 += hit
        mrr += 1 / rank if rank else 0
        if not hit:
            rows.append({"id": case["id"], "query": case["query"], "got": got, "want": sorted(want)})
    n = len(_jsonl("retrieval"))
    return {"recall_at_3": round(hits3 / n, 3), "mrr": round(mrr / n, 3), "n": n,
            "misses": rows[:20], "cost_usd": round(trace.cost_usd, 4)}


async def suite_ask(app) -> dict:
    from daybook.agents.ask import ask
    from daybook.obs.tracer import Trace

    vault, keys = await _seed_vault(app)
    trace = Trace("eval")
    tp_abs = fn_abs = fp_abs = 0
    cited_ok = cited_total = 0
    rows = []
    for case in _jsonl("ask"):
        out = await ask(app.deps, vault, app.embedder, case["question"],
                        date.fromisoformat(case["today"]), trace, app.settings.abstain_cosine)
        if not case["answerable"]:
            tp_abs += out["abstained"]
            fn_abs += not out["abstained"]
            if not out["abstained"]:
                rows.append({"id": case["id"], "q": case["question"], "answered": out["answer"]})
        else:
            fp_abs += out["abstained"]
            for s in out["sentences"]:
                cited_total += 1
                cited_ok += bool(s["item_ids"])
            if out["abstained"]:
                rows.append({"id": case["id"], "q": case["question"], "abstained": True})
    n_un = tp_abs + fn_abs
    return {"abstain_recall": round(tp_abs / n_un, 3) if n_un else None,
            "abstain_precision": round(tp_abs / (tp_abs + fp_abs), 3) if tp_abs + fp_abs else None,
            "citation_rate": round(cited_ok / cited_total, 3) if cited_total else None,
            "issues": rows[:20], "cost_usd": round(trace.cost_usd, 4)}


# ------------------------------------------------------------------ runner


def _sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       text=True).strip()
    except Exception:
        return "nogit"


def estimate(suites: list[str]) -> float:
    sizes = {"router": len(_jsonl("router")), "extraction": len(_jsonl("extraction")),
             "retrieval": len(_jsonl("retrieval")), "ask": len(_jsonl("ask")), "guards": 0}
    return round(sum(COST_PER_CASE[s] * sizes[s] for s in suites), 3)


async def run(suites: list[str], mode: str) -> dict:
    out = {"created_at": datetime.now(UTC).isoformat(timespec="seconds"), "mode": mode,
           "git_sha": _sha(), "suites": {}}
    for name in suites:
        tmp = Path(tempfile.mkdtemp())
        app = _app(mode, tmp)
        res = await globals()[f"suite_{name}"](app)
        metric, gate = GATES[name]
        score = res.get(metric)
        passed = score is not None and score >= gate
        if name == "extraction":
            passed = passed and res["invented_number_rate"] == 0
        out["suites"][name] = {"score": score, "metric": metric, "gate": gate, "passed": passed,
                               "details": res}
        print(f"{name:11} {metric}={score} (gate {gate}) {'PASS' if passed else 'FAIL'}")
    from daybook.prompts import load

    out["prompt_versions"] = {n: load(n)[1] for n in (
        "researcher", "analyzer_fast", "analyzer_deep", "chat", "ask", "context_verify")}
    return out


def main() -> None:
    p = argparse.ArgumentParser(prog="evals")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--suite", default="all")
    r.add_argument("--mode", choices=["fake", "live"], default="fake")
    r.add_argument("--yes", action="store_true", help="confirm a live run")
    a = p.parse_args()
    suites = SUITES if a.suite == "all" else a.suite.split(",")
    if a.mode == "live":
        est = estimate(suites)
        print(f"Live run estimate: ${est:.2f} (refused above $2.00)")
        if est > 2.0:
            sys.exit("refused: estimate above $2")
        if not a.yes:
            sys.exit("add --yes to confirm the live run (and get Ayan's sign-off first, plan 13.4)")
    result = asyncio.run(run(suites, a.mode))
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = RESULTS / f"{stamp}-{result['git_sha']}-{a.mode}.json"
    path.write_text(json.dumps(result, indent=1, default=str))
    lines = [f"# Eval run {stamp} ({a.mode}, {result['git_sha']})", ""]
    for n, s in result["suites"].items():
        lines.append(f"- **{n}**: {s['metric']} = {s['score']} (gate {s['gate']}) "
                     f"{'PASS' if s['passed'] else 'FAIL'}")
    (RESULTS / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\nwrote {path.relative_to(ROOT)}")
    # fake mode only checks plumbing: its only meaningful gate is the code-only guards suite
    enforced = result["suites"] if a.mode == "live" else {
        k: v for k, v in result["suites"].items() if k == "guards"}
    if not all(s["passed"] for s in enforced.values()):
        sys.exit(1)
