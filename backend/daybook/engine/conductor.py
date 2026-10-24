"""The Analyzer's orchestration: plain Python that runs the steps of one turn in a fixed order
(plan 5.3). No LLM decides which agent runs.

  load session → time check → safety pre-check → Researcher ∥ fast pass → merge + validate the
  board → agenda.decide → (Context agent / answer parsing / confirmed writes) → Chat agent →
  guards (regenerate once, then template) → save session + trace → reply
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from daybook.agents import analyzer_fast, chat, context, researcher
from daybook.agents.schemas import DraftDelta, TurnLabel
from daybook.engine import agenda, guards, phases, safety
from daybook.engine import state as st
from daybook.engine.agenda import Move
from daybook.engine.app import App, User
from daybook.engine.state import PendingActionState, PendingAsk, SessionState, TurnRec
from daybook.memory import repo
from daybook.memory.validate import number_supported, quote_in
from daybook.obs.log import log_event
from daybook.obs.tracer import Trace
from daybook.storage.blob import PreconditionFailed


class SessionError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def local_date(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def _iso(now: datetime) -> str:
    return now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")


# ------------------------------------------------------------------ views


def board_view(state: SessionState) -> dict:
    gaps_by = {}
    for g in state.gaps:
        gaps_by.setdefault(g["temp_id"], []).append(g["gap"])
    items = []
    for tid, it in state.board_items.items():
        metrics = [m["text"] for m in it.get("metrics_raw", []) if m.get("ok", True)]
        if it.get("kind") == "soft_skill":
            chip = "soft_skill"
        elif metrics:
            chip = "number_captured"
        elif "metric" in gaps_by.get(tid, []) or it.get("quantifiable") == "yes":
            chip = "needs_number"
        elif "outcome" in gaps_by.get(tid, []):
            chip = "needs_outcome"
        else:
            chip = "captured"
        items.append({"temp_id": tid, "kind": it["kind"], "title": it["title"], "chip": chip,
                      "metrics_text": metrics})
    return {"items": items}


def pending_action_view(state: SessionState) -> dict | None:
    pa = state.pending_action
    return {"kind": pa.kind, "prompt": pa.prompt} if pa and pa.kind != "find_item" else None


def session_view(state: SessionState, now: datetime, suggestions: list[str] | None = None) -> dict:
    return {
        "id": state.id, "mode": state.mode, "status": state.status, "phase": state.phase,
        "limit_sec": state.limit_sec, "started_at": state.started_at,
        "seconds_left": state.seconds_left(now) if state.status == "active" else 0,
        "voice_enabled": state.voice_enabled,
        "turns": [
            {"seq": t.seq, "role": t.role, "text": t.text, "created_at": t.created_at,
             "turn_id": t.turn_id, "safety": t.safety}
            for t in state.turns
        ],
        "board": board_view(state),
        "pending_action": pending_action_view(state),
        "suggestions": suggestions or [],
    }


# ------------------------------------------------------------------ persistence helpers


def _save(app: App, user: User, state: SessionState, etag: str | None) -> str:
    return st.save(app.store, user.prefix, state, etag)


def _save_trace(app: App, user: User, state: SessionState, seq: int, trace: Trace, **meta) -> dict:
    d = trace.finish(**meta)
    key = f"traces/{user.uid}/{state.local_date}/{state.id}/{seq:03}.json"
    try:
        app.store.put(key, Trace.dumps(d), content_type="application/json")
    except Exception:
        log_event(event="trace_write_failed", session_id=state.id)
    return d


def active_pointer(app: App, user: User) -> str | None:
    b = app.store.get(f"{user.prefix}active.json")
    if not b:
        return None
    import json

    return json.loads(b.data).get("session_id")


def _set_active(app: App, user: User, sid: str | None) -> None:
    import json

    app.store.put(f"{user.prefix}active.json", json.dumps({"session_id": sid}).encode())


# ------------------------------------------------------------------ speaking (Chat + guards)


async def speak(app: App, state: SessionState, move: Move, trace: Trace, now: datetime,
                last_user: str, extra_sources: list[str] = ()) -> str:
    sources = [*state.user_texts(), *move.say_back, move.template, *(extra_sources or [])]
    if move.about:
        sources.append(move.about)
    gctx = guards.GuardContext(sources=sources, last_user_text=last_user, max_words=move.max_words,
                               wrap=move.wrap, promised=move.promised)
    if not move.use_llm:
        with trace.span("guards", "code", **{"daybook.guard": "template"}):
            pass
        return move.template
    note = move.note(state, now)
    turns = state.turn_dicts()
    text, resp = await chat.reply(app.deps, trace, turns, note)
    results: dict[str, bool] = {}
    for attempt in range(2):
        if text:
            text = guards.strip_markdown(text)
            results = guards.check(text, gctx)
            failed = guards.failures(results)
            if not failed:
                break
            for f in failed:
                state.guard_fails[f] = state.guard_fails.get(f, 0) + 1
            if "fidelity" in failed:
                log_event(event="guard_fidelity_fail", session_id=state.id, move=move.name)
            if attempt == 0:
                text, resp = await chat.reply(
                    app.deps, trace, turns,
                    note + "\ncorrection: " + guards.correction_note(failed, gctx), attempt=1,
                )
                continue
        text = None
        break
    with trace.span("guards", "code", **{"daybook.guard": results,
                                         "daybook.fallback": text is None}):
        pass
    if resp is not None and resp.blocked:
        log_event(event="chat_blocked", session_id=state.id)
        return "Let's keep going. What else happened today?" if not move.end else move.template
    return text or move.template


# ------------------------------------------------------------------ start


async def start_session(
    app: App, user: User, mode: str, now: datetime, item_id: int | None = None,
    limit_override: int | None = None,
) -> SessionState:
    sid_active = active_pointer(app, user)
    if sid_active:
        loaded = st.load(app.store, user.prefix, sid_active)
        if loaded and loaded[0].status == "active":
            if phases.past_grace(loaded[0], now):
                await end_session(app, user, sid_active, "time_limit", now)
            else:
                raise SessionError(409, "session_active", "A reflection is already running.",
                                   session_id=sid_active)
    vault = app.vault(user)
    with vault.read() as conn:
        profile = repo.get_profile(conn) or {}
        tz = profile.get("timezone") or app.settings.tz_default
        today = local_date(now, tz)
        prefs = repo.get_setting(conn, "app_settings", {}) or {}
        projects = repo.list_projects(conn)
        pending = repo.open_pending(conn, today.isoformat()) if mode == "guided" else []
        plan = conn.execute(
            "SELECT title FROM memory_items WHERE kind = 'plan' AND occurred_on < ?"
            " AND status != 'hidden' ORDER BY occurred_on DESC, id DESC LIMIT 1",
            (today.isoformat(),),
        ).fetchone()
        edit_item = repo.get_item(conn, item_id) if (mode == "edit" and item_id) else None
    if mode == "edit" and not edit_item:
        raise SessionError(404, "not_found", "That memory doesn't exist.")
    if prefs.get("pending_at") == "end":
        pending = []  # asked at the end instead: see REVIEW_QUEUE RQ-30
    if user.role == "demo":
        limit = app.settings.demo_session_limit_sec
    else:
        limit = limit_override or int(prefs.get("session_limit_sec") or app.settings.session_limit_sec)
    state = SessionState(
        id=uuid.uuid4().hex[:16], uid=user.uid, role=user.role, mode=mode,
        local_date=today.isoformat(), tz=tz, started_at=now.isoformat(), limit_sec=limit,
        phase={"guided": "open", "dump": "dump", "quick": "capture", "edit": "edit"}[mode],
        pending_queue=[
            PendingAsk(id=p["id"], question=p["question"], kind=p["kind"], item_id=p["item_id"],
                       item_title=p["item_title"])
            for p in pending[: phases.MAX_PENDING_PER_SESSION]
        ],
        tts_char_limit=app.settings.demo_tts_chars if user.role == "demo"
        else app.settings.session_tts_chars,
        voice_enabled=bool(prefs.get("voice", True)) and app.tts is not None,
        opener_context={"projects": [{"id": p["id"], "name": p["name"], "aliases": p["aliases"]}
                                     for p in projects],
                        "display_name": profile.get("display_name")},
        edit_item_id=item_id if mode == "edit" else None,
    )
    if app.spend.status(user.role, now) == "over":
        state.voice_enabled = False
    trace = Trace("turn", session_id=state.id, seq=1, user_text=None)
    if mode == "edit":
        fact = context.record_fact(edit_item)
        move = Move("open_edit", f"{fact} What should I change?", say_back=[fact], use_llm=False,
                    max_words=70)
        state.pending_action = PendingActionState(
            kind="describe_edit", prompt="Say what to change.",
            data={"item_id": item_id, "tries": 0},
        )
    else:
        move = agenda.opener(state, profile.get("display_name"), plan["title"] if plan else None)
    with trace.span("analyzer.decide", "code", **{"daybook.move": move.name,
                                                  "daybook.phase": state.phase}):
        pass
    text = await speak(app, state, move, trace, now, "")
    if move.name == "open_pending":
        state.phase = "close_loops"
        first = state.pending_queue[0]
        state.current_pending = first
        state.asked_pending.append(first.id)
    elif mode == "guided":
        state.phase = "explore"
    _append_assistant(state, text, move, now)
    _account(app, user, state, trace, now)
    _save(app, user, state, None)
    _set_active(app, user, state.id)
    _save_trace(app, user, state, 1, trace, reply_text=text, move=move.name)
    return state


def _append_assistant(state: SessionState, text: str, move: Move, now: datetime) -> TurnRec:
    seq = state.next_seq()
    t = TurnRec(seq=seq, role="assistant", text=text, created_at=_iso(now),
                turn_id=f"{state.id}.{seq}", move=move.name, safety=move.name == "safety")
    state.turns.append(t)
    return t


def _account(app: App, user: User, state: SessionState, trace: Trace, now: datetime) -> None:
    state.budgets["tokens_in"] += trace.tokens_in
    state.budgets["tokens_out"] += trace.tokens_out
    state.budgets["cost_usd"] = round(state.budgets["cost_usd"] + trace.cost_usd, 6)
    app.spend.add(user.role, trace.cost_usd, now)
    for s in trace.spans:
        v = s.attrs.get("daybook.prompt_version")
        if v:
            state.prompt_versions[v.split("@")[0]] = v


# ------------------------------------------------------------------ the turn


async def handle_turn(
    app: App, user: User, sid: str, client_turn_id: str, text: str, dictated_chars: int,
    now: datetime,
) -> dict:
    for attempt in range(3):
        loaded = st.load(app.store, user.prefix, sid)
        if loaded is None:
            raise SessionError(404, "not_found", "No such session.")
        state, etag = loaded
        if client_turn_id in state.client_turns:
            return state.client_turns[client_turn_id]
        if state.status != "active":
            raise SessionError(409, "session_ended", "This reflection has ended.")
        if phases.past_grace(state, now):
            await end_session(app, user, sid, "time_limit", now)
            raise SessionError(409, "time_up", "Time's up for today's reflection.")
        result = await _run_turn(app, user, state, text, dictated_chars, now)
        state.client_turns[client_turn_id] = result
        try:
            _save(app, user, state, etag)
        except PreconditionFailed:
            log_event(event="session_conflict", session_id=sid, attempt=attempt)
            continue  # another tab wrote first: reload and redo (idempotent on client_turn_id)
        if result["ended"]:
            await _finish(app, user, state, now)
        return result
    raise SessionError(409, "conflict", "The session changed in another tab. Please retry.")


async def _run_turn(app: App, user: User, state: SessionState, text: str, dictated: int,
                    now: datetime) -> dict:
    text = text.strip()[:4000]
    seq = state.next_seq()
    crisis = safety.is_crisis(text)
    trace = Trace("turn", session_id=state.id, seq=seq,
                  user_text=None if crisis else text)
    state.turns.append(TurnRec(seq=seq, role="user", text=text, created_at=_iso(now),
                               dictated_chars=max(0, min(dictated or 0, len(text)))))
    if crisis:
        return _safety_reply(app, user, state, seq, trace, now, "precheck")

    today = date.fromisoformat(state.local_date)
    pa = state.pending_action
    awaiting = pa.prompt if pa else None
    pending = ({"id": state.current_pending.id, "question": state.current_pending.question}
               if state.current_pending else None)
    run_fast = state.mode != "edit"
    board = {k: {kk: vv for kk, vv in v.items() if kk not in ("ok",)}
             for k, v in state.board_items.items()}
    tasks = [researcher.label_turn(app.deps, trace, text, today, state.tz,
                                   state.opener_context.get("projects", []), pending,
                                   state.last_assistant(), awaiting)]
    if run_fast:
        tasks.append(analyzer_fast.fast_pass(app.deps, trace, state.turn_dicts(), board, today,
                                             state.mode))
    out = await asyncio.gather(*tasks)
    label: TurnLabel = out[0]
    delta: DraftDelta | None = out[1] if run_fast else None
    state.turns[-1].intent = label.primary_intent
    if label.primary_intent == "crisis_signal":
        state.turns[-1].text = "(not saved)"
        return _safety_reply(app, user, state, seq, trace, now, "researcher")

    applies = _delta_applies(state, label)
    if delta and applies:
        with trace.span("analyzer.merge", "code") as s:
            s.attrs.update(_merge_delta(state, delta))
        if state.phase == "wrap" and delta.items:
            state.flags["wrap_added"] = True
    if label.primary_intent == "defer" and not state.current_pending:
        tgt = state.flags.get("last_target")
        if tgt:  # "not sure about the number": don't ask again today, ask tomorrow
            state.followups[tgt] = phases.MAX_FOLLOWUPS_PER_ITEM

    with trace.span("analyzer.decide", "code") as dspan:
        step = agenda.decide(state, label, now)
        dspan.attrs["daybook.step"] = step.kind
    move, extra_sources = await _resolve(app, user, state, step, label, text, trace, now)
    _apply_move_effects(state, move)
    dspan.attrs.update({"daybook.move": move.name, "daybook.target": move.target,
                        "daybook.gap": move.gap, "daybook.phase": state.phase,
                        "daybook.seconds_left": state.seconds_left(now)})
    reply = await speak(app, state, move, trace, now, text, extra_sources)
    a = _append_assistant(state, reply, move, now)
    if move.end:
        state.phase = "done"
        state.status = "ended"
        state.ended_at = _iso(now)
        state.end_reason = "time_limit" if phases.over_limit(state, now) else "user"
    if len([t for t in state.turns if t.role == "user"]) >= app.settings.session_max_turns and not move.end:
        state.flags["turn_budget_hit"] = True
    if state.budgets["tokens_in"] > app.settings.session_max_tokens_in:
        state.voice_enabled = False
    _account(app, user, state, trace, now)
    d = _save_trace(app, user, state, seq, trace, user_text=text, reply_text=reply, move=move.name, intent=label.primary_intent,
                    injection_suspected=label.injection_suspected)
    state.turn_latencies.append(d["totals"]["latency_ms"])
    if user.role == "demo":
        state.flags["last_trace"] = d
    return _result(state, a, move, now)


def _result(state: SessionState, a: TurnRec, move: Move, now: datetime, safety_info=None) -> dict:
    return {
        "turn_id": a.turn_id, "reply_text": a.text, "phase": state.phase,
        "seconds_left": state.seconds_left(now) if state.status == "active" else 0,
        "board": board_view(state), "pending_action": pending_action_view(state),
        "ended": state.status != "active", "voice": state.voice_enabled and not safety_info,
        "safety": safety_info, "suggestions": [],
    }


def _safety_reply(app, user, state, seq, trace, now, source: str) -> dict:
    state.turns[-1].text = "(not saved)"
    state.turns[-1].safety = True
    with trace.span("safety", "code", **{"daybook.safety.source": source}):
        pass
    move = Move("safety", safety.MESSAGE, use_llm=False)
    a = _append_assistant(state, safety.MESSAGE, move, now)
    state.flags["safety_ran"] = True
    _account(app, user, state, trace, now)
    _save_trace(app, user, state, seq, trace, user_text=None, reply_text="(safety template)",
                move="safety")
    res = _result(state, a, move, now, safety.safety_payload())
    res["voice"] = state.voice_enabled
    return res


def _delta_applies(state: SessionState, label: TurnLabel) -> bool:
    if state.pending_action is not None or state.current_pending is not None:
        return "reflect" in label.secondary_intents
    return label.primary_intent in (
        "reflect", "vent", "metric_answer", "not_quantifiable", "defer", "support_request"
    ) or "reflect" in label.secondary_intents or (
        label.primary_intent == "meta" and label.meta_command == "wrap_up"
    )


def _merge_delta(state: SessionState, delta: DraftDelta) -> dict:
    """Upsert items, validate quotes and numbers against the user's words, replace gaps."""
    users = state.user_texts()
    bad_quotes, dropped_numbers = [], []
    for it in delta.items:
        d = it.model_dump()
        if not quote_in(d["source_quote"], users):
            bad_quotes.append(it.temp_id)
            d["quote_ok"] = False
        else:
            d["quote_ok"] = True
        kept = []
        for m in d["metrics_raw"]:
            ok = all(number_supported(m.get(k), users) for k in ("baseline", "final", "delta_pct"))
            ok = ok and quote_in(m["text"], users)
            if ok:
                kept.append(m)
            else:
                dropped_numbers.append(m["text"])
        d["metrics_raw"] = kept
        prev = state.board_items.get(it.temp_id)
        if prev:  # keep metrics captured earlier
            texts = {m["text"] for m in kept}
            d["metrics_raw"] = kept + [m for m in prev.get("metrics_raw", []) if m["text"] not in texts]
        state.board_items[it.temp_id] = d
    if delta.gaps or delta.items:
        gaps = []
        for g in delta.gaps:
            item = state.board_items.get(g.temp_id)
            if not item:
                continue
            if g.gap == "metric" and item.get("metrics_raw"):
                continue  # filled
            gaps.append(g.model_dump())
        state.gaps = gaps
    if delta.energy:
        state.energy = delta.energy
    if delta.mood_word:
        state.mood_word = delta.mood_word
    return {"daybook.board.items": len(state.board_items), "daybook.board.gaps": len(state.gaps),
            "daybook.validate.bad_quotes": bad_quotes,
            "daybook.validate.dropped_numbers": dropped_numbers}


def _apply_move_effects(state: SessionState, move: Move) -> None:
    if move.name == "ask_pending":
        p = next((q for q in state.pending_queue if q.id == move.pending_id), None)
        if p:
            state.current_pending = p
            if p.id not in state.asked_pending:
                state.asked_pending.append(p.id)
        state.phase = "close_loops"
        return
    if state.phase == "close_loops" and move.name not in (
        "confirm_metric", "repeat", "time_left", "pause", "reflect_vent", "support", "steer_back"
    ):
        state.phase = "explore"
    if move.name == "ask_gap" and move.target:
        state.followups[move.target] = state.followups.get(move.target, 0) + 1
        state.flags["last_target"] = move.target
        if state.mode == "dump":
            state.followups_asked += 1
            state.phase = "followups"
        elif state.phase in ("open", "close_loops"):
            state.phase = "explore"
    elif move.name.startswith("ask_") and move.name != "ask_pending":
        key = {"ask_hard_part": "hard_part", "ask_learning": "learning",
               "ask_tomorrow": "tomorrow", "ask_open": "what_else"}.get(move.name)
        if key:
            state.open_prompts_done.append(key)
        state.flags["last_target"] = None
    if move.name == "wrap":
        state.phase = "wrap"


# ------------------------------------------------------------------ async steps


async def _resolve(app: App, user: User, state: SessionState, step: agenda.Step, label: TurnLabel,
                   text: str, trace: Trace, now: datetime) -> tuple[Move, list[str]]:
    k = step.kind
    if k == "move":
        return step.move, []
    if k == "safety":  # handled earlier; keep a guard here
        return Move("safety", safety.MESSAGE, use_llm=False), []
    if k == "pending_answer":
        return await _pending_answer(app, user, state, label, text, trace, now), []
    if k in ("confirm", "deny"):
        return await _confirm(app, user, state, k == "confirm", text, trace, now)
    if k == "edit_search":
        return await _edit_search(app, user, state, label, text, trace, now)
    if k == "find_again":
        pa = state.pending_action
        hint = f"{pa.data.get('request', '')} {text}".strip()
        return await _edit_search(app, user, state, label, hint, trace, now,
                                  tries=pa.data.get("tries", 1), request=pa.data.get("request"))
    if k == "pick":
        return await _pick(app, user, state, label, text, trace, now)
    if k == "describe_edit":
        return await _describe_edit(app, user, state, text, trace, now)
    if k == "recall_search":
        return await _recall(app, user, state, label, text, trace, now)
    raise ValueError(k)


def _tomorrow(state: SessionState) -> str:
    return (date.fromisoformat(state.local_date) + timedelta(days=1)).isoformat()


def _after(state: SessionState, now: datetime, ack: str, ack_template: str) -> Move:
    return agenda.next_move(state, now, ack=ack, ack_template=ack_template)


async def _pending_answer(app, user, state: SessionState, label, text, trace, now) -> Move:
    p = state.current_pending
    if p is None and state.pending_action and state.pending_action.kind == "confirm_metric":
        p = PendingAsk.model_validate(state.pending_action.data["pending"])
    if p is None:
        return agenda.next_move(state, now)
    intent = label.primary_intent
    vault = app.vault(user)

    def resolve(status: str, answer: str | None):
        state.resolved_pending.append({"pending_id": p.id, "resolution": status,
                                       "item_id": p.item_id, "answer_text": answer})
        if p.item_id and p.item_id not in state.touched_items:
            state.touched_items.append(p.item_id)
        state.current_pending = None
        state.pending_action = None

    if intent == "defer":
        vault.write(lambda c: repo.snooze_pending(c, p.id, _tomorrow(state)))
        resolve("deferred", text)
        return _after(state, now, "Say it's no problem and you'll ask again tomorrow.",
                      "No problem, I'll ask again tomorrow.")
    if intent == "not_quantifiable":
        vault.write(lambda c: repo.resolve_pending(c, p.id, "not_quantifiable", text))
        resolve("not_quantifiable", text)
        return _after(state, now, "Say that's fine; you'll keep it as a qualitative one.",
                      "That's fine, I'll keep it as a qualitative one.")
    with vault.read() as conn:
        item = repo.get_item(conn, p.item_id) if p.item_id else None
    ans = await analyzer_fast.parse_pending_answer(
        app.deps, trace, p.question,
        {"title": item["title"], "what": item["what"], "result": item["result"]} if item else None,
        text,
    )
    if ans is None:
        state.current_pending = None
        return _after(state, now, "Thank them briefly.", "Thanks.")
    if ans.kind == "defer":
        label.primary_intent = "defer"
        return await _pending_answer(app, user, state, label, text, trace, now)
    if ans.kind == "not_quantifiable":
        label.primary_intent = "not_quantifiable"
        return await _pending_answer(app, user, state, label, text, trace, now)
    if ans.kind == "metric" and ans.metric:
        m = ans.metric
        users = [text]
        ok = quote_in(m.text, users) and all(
            number_supported(getattr(m, f), users) for f in ("baseline", "final", "delta_pct")
        )
        if ok:
            metric = {"name": ans.metric_name or (item["title"] if item else "result"),
                      "category": ans.category or "other", "unit": m.unit or "count",
                      "baseline": m.baseline, "final": m.final, "delta_pct": m.delta_pct,
                      "better_when": ans.better_when or "higher", "source_text": m.text,
                      "approx": bool(re.search(r"\b(about|around|roughly|~|under|over|nearly)\b",
                                               m.text, re.I))}
            readback = ans.readback if not guards.failures(guards.check(
                ans.readback, guards.GuardContext(sources=[text, p.question]))) \
                else f"So that's {m.text}?"
            state.pending_action = PendingActionState(
                kind="confirm_metric", prompt="Is that right?",
                data={"pending": p.model_dump(), "metric": metric, "answer_text": text},
            )
            state.current_pending = None
            return Move("confirm_metric", readback, use_llm=False, say_back=[readback],
                        pending_id=p.id)
    # an answer without a number (outcome / ownership)
    vault.write(lambda c: repo.resolve_pending(c, p.id, "answered", text))
    resolve("answered", text)
    return _after(state, now, "Thank them in a few words for filling that in.", "Thanks, got it.")


async def _confirm(app, user, state: SessionState, yes: bool, text, trace, now):
    pa = state.pending_action
    vault = app.vault(user)
    if pa.kind == "confirm_metric":
        p = PendingAsk.model_validate(pa.data["pending"])
        state.pending_action = None
        if not yes:
            state.current_pending = p
            return Move("ask_pending", "No problem. What's the right number?", use_llm=False,
                        pending_id=p.id), []
        metric, answer = pa.data["metric"], pa.data["answer_text"]

        def write(conn):
            if p.item_id:
                repo.add_metric(conn, p.item_id, metric)
                item = repo.get_item(conn, p.item_id)
                others = [q for q in item["open_pending"] if q["id"] != p.id]
                fields = {"status": "active"} if item["status"] == "draft" and not others \
                    and not item["validation_note"] else {}
                repo.update_item(conn, p.item_id, fields, "pending_answer",
                                 f"answered: {p.question}")
            repo.resolve_pending(conn, p.id, "answered", answer)

        vault.write(write)
        state.resolved_pending.append({"pending_id": p.id, "resolution": "answered",
                                       "item_id": p.item_id, "answer_text": answer})
        if p.item_id and p.item_id not in state.touched_items:
            state.touched_items.append(p.item_id)
        return _after(state, now, "Say it's saved, in two or three words.", "Great, saved."), []

    if pa.kind == "confirm_item":
        item_id = pa.data["item_id"]
        if not yes:
            state.pending_action = PendingActionState(
                kind="find_item", prompt="", data={"request": pa.data.get("request", ""),
                                                   "tries": pa.data.get("tries", 1)})
            return Move("edit_not_found", "Okay. Which one did you mean? Roughly when was it?",
                        use_llm=False), []
        return await _propose(app, user, state, item_id, pa.data.get("request") or text, trace, now)

    if pa.kind == "confirm_edit":
        state.pending_action = None
        if not yes:
            return _edit_done(state, now, "Okay, I've left it as it was."), []
        item_id, patch = pa.data["item_id"], pa.data["patch"]
        sources = pa.data.get("sources", [])
        users = [*sources, text]
        changes = {k: patch[k] for k in ("title", "what", "how", "result", "occurred_on")
                   if patch.get(k)}
        mu, add = [], []
        for mc in patch.get("metric_changes", []):
            if not all(number_supported(mc.get(f), users) for f in ("baseline", "final", "delta_pct")):
                continue
            if mc.get("metric_id"):
                mu.append({"id": mc["metric_id"], **{k: mc.get(k) for k in
                           ("baseline", "final", "delta_pct", "source_text", "unit", "name")}})
            else:
                add.append({**mc, "category": "other", "better_when": "higher"})

        def write(conn):
            repo.update_item(conn, item_id, changes, "user_voice", f"voice edit: {text[:120]}",
                             metric_updates=mu, add_metrics=add)

        vault.write(write)
        state.touched_items.append(item_id)
        state.flags.setdefault("edited_items", []).append(item_id)
        return _edit_done(state, now, "Done, updated."), []
    state.pending_action = None
    return agenda.next_move(state, now), []


def _edit_done(state: SessionState, now: datetime, line: str) -> Move:
    if state.mode == "edit":
        return Move("goodbye", f"{line} Thanks.", end=True, use_llm=False)
    nxt = agenda.next_move(state, now, ack_template=line)
    nxt.ack = f"First say exactly: '{line}'"
    return nxt


async def _propose(app, user, state, item_id: int, request: str, trace, now):
    with app.vault(user).read() as conn:
        item = repo.get_item(conn, item_id)
    record = {"id": item["id"], "date": item["occurred_on"], "title": item["title"],
              "what": item["what"], "how": item["how"], "result": item["result"],
              "metrics": [{"metric_id": m["id"], "name": m["name"], "unit": m["unit"],
                           "baseline": m["baseline"], "final": m["final"],
                           "delta_pct": m["delta_pct"], "source_text": m["source_text"]}
                          for m in item["metrics"]]}
    patch = await analyzer_fast.propose_edit(app.deps, trace, record, request)
    sources = [request]
    if patch is None or not patch.understood or not patch.new_value_text:
        state.pending_action = PendingActionState(
            kind="describe_edit", prompt="Say what to change.",
            data={"item_id": item_id, "tries": 1})
        return Move("describe_edit", "Got it, that one. What should I change about it?",
                    use_llm=False), [context.record_fact(item)]
    bad = [mc for mc in patch.metric_changes
           if not all(number_supported(getattr(mc, f), sources) for f in ("baseline", "final", "delta_pct"))]
    if bad and not patch.result:
        state.pending_action = PendingActionState(
            kind="describe_edit", prompt="Say the new value.", data={"item_id": item_id, "tries": 1})
        return Move("describe_edit", "What's the new value, exactly?", use_llm=False), []
    line = f"So I'll change {patch.old_value_text} to {patch.new_value_text}. Okay?"
    state.pending_action = PendingActionState(
        kind="confirm_edit", prompt=line,
        data={"item_id": item_id, "patch": patch.model_dump(), "sources": sources})
    return Move("confirm_edit", line, use_llm=False, say_back=[line]), [context.record_fact(item)]


async def _edit_search(app, user, state, label: TurnLabel, hint: str, trace, now, tries: int = 0,
                       request: str | None = None):
    request = request or hint
    q = label.entities.target_hint or hint
    res = await context.find(app.deps, app.vault(user), app.embedder, q if tries == 0 else hint,
                             label.entities.time_refs, trace, app.settings.abstain_cosine)
    if res.status == "found":
        c = res.candidates[0]
        fact = c.field_values["fact"]
        state.pending_action = PendingActionState(
            kind="confirm_item", prompt="Is this the one?",
            data={"item_id": c.item_id, "request": request, "tries": tries + 1})
        return Move("edit_readback", f"{fact} That one?", say_back=[fact],
                    instruction="Read back the record fact word for word, then ask 'That one?'",
                    max_words=60), [fact]
    if res.status == "ambiguous" and len(res.candidates) > 1:
        names = [c.title for c in res.candidates[:2]]
        state.pending_action = PendingActionState(
            kind="pick_item", prompt="Which one?",
            data={"candidates": [c.model_dump() for c in res.candidates[:3]], "request": request,
                  "tries": tries + 1})
        line = f"I have two that could fit: {names[0]}, and {names[1]}. Which one?"
        return Move("edit_pick", line, say_back=names, max_words=60,
                    instruction="Name the two candidates and ask which one."), names
    if tries >= 1:
        state.pending_action = None
        return Move("edit_give_up", "I still can't find that one. You can fix it from the "
                    "Timeline anytime.", use_llm=False), []
    state.pending_action = PendingActionState(kind="find_item", prompt="",
                                              data={"request": request, "tries": tries + 1})
    return Move("edit_not_found", "I couldn't find that one. Roughly when was it?",
                use_llm=False), []


_ORDINALS = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "other": 1,
             "latter": 1, "former": 0, "third": 2}


async def _pick(app, user, state, label, text, trace, now):
    pa = state.pending_action
    cands = pa.data.get("candidates") or []
    low = text.lower()
    chosen = None
    if cands:
        for w, i in _ORDINALS.items():
            if re.search(rf"\b{w}\b", low) and i < len(cands):
                chosen = cands[i]
                break
        if chosen is None:
            def overlap(c):
                words = {w for w in re.findall(r"[a-z0-9]+", c["title"].lower()) if len(w) > 2}
                return len(words & set(re.findall(r"[a-z0-9]+", low)))
            best = max(cands, key=overlap)
            if overlap(best) > 0:
                chosen = best
    if chosen:
        return await _propose(app, user, state, chosen["item_id"], pa.data.get("request") or text,
                              trace, now)
    if pa.kind == "confirm_item" or pa.data.get("tries", 1) >= 2:
        hint = f"{pa.data.get('request', '')} {text}"
        return await _edit_search(app, user, state, label, hint, trace, now,
                                  tries=pa.data.get("tries", 1), request=pa.data.get("request"))
    pa.data["tries"] = pa.data.get("tries", 1) + 1
    names = [c["title"] for c in cands[:2]]
    return Move("edit_pick", f"Sorry, which one: {names[0]}, or {names[1]}?", use_llm=False), names


async def _describe_edit(app, user, state, text, trace, now):
    pa = state.pending_action
    if pa.data.get("tries", 0) >= 3:
        state.pending_action = None
        return _edit_done(state, now, "Let's leave it for now; you can edit it from the Timeline."), []
    pa.data["tries"] = pa.data.get("tries", 0) + 1
    return await _propose(app, user, state, pa.data["item_id"], text, trace, now)


async def _recall(app, user, state, label, text, trace, now):
    q = label.entities.target_hint or text
    res = await context.find(app.deps, app.vault(user), app.embedder, q, label.entities.time_refs,
                             trace, app.settings.abstain_cosine, verify=False)
    if res.status == "none" or not res.candidates:
        return Move("recall_none", "I don't have anything about that yet.", use_llm=False), []
    facts = [c.field_values["fact"] for c in res.candidates[:2]]
    return Move("recall_answer", " ".join(facts[:1]), say_back=facts, max_words=60,
                instruction="Answer in one or two sentences using only the say_back facts. "
                            "Then, without a question or with one gentle one, return to today."), facts


# ------------------------------------------------------------------ end


async def end_session(app: App, user: User, sid: str, reason: str, now: datetime) -> SessionState:
    for _ in range(3):
        loaded = st.load(app.store, user.prefix, sid)
        if loaded is None:
            raise SessionError(404, "not_found", "No such session.")
        state, etag = loaded
        if state.status != "active":
            return state
        state.status = "ended"
        state.phase = "done"
        state.end_reason = reason if reason in ("user", "time_limit", "idle", "budget", "error") \
            else "user"
        state.ended_at = _iso(now)
        try:
            _save(app, user, state, etag)
        except PreconditionFailed:
            continue
        await _finish(app, user, state, now)
        return state
    raise SessionError(409, "conflict", "Please retry.")


async def _finish(app: App, user: User, state: SessionState, now: datetime) -> None:
    if active_pointer(app, user) == state.id:
        _set_active(app, user, None)
    app.dispatch("finalize_session", uid=user.uid, prefix=user.prefix,
                 sandbox_id=user.sandbox_id, session_id=state.id)
