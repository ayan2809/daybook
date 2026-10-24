"""Offline LLMs (plan 6.1, D-12; DB-07).

- `FakeLLM`: Lore's scripted fake. Plays back responses in order and records every request.
- `HeuristicFakeLLM`: answers each response schema with rule-based output so the whole app runs
  with no keys: keyword intents, sentence-level draft items, template chat lines (the operator
  note's `example`), a board-based deep pass, keyword-overlap Ask.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from daybook.agents import schemas as S
from daybook.llm.gemini import LLMResponse
from daybook.memory.embed import fake_embedding
from daybook.memory.validate import numbers_in

Script = LLMResponse | Callable[..., LLMResponse]


def _text_of(content) -> str:
    return "".join(getattr(p, "text", "") or "" for p in (content.parts or []))


class FakeLLM:
    """Plays back scripted responses in order. Records every request in `self.calls`."""

    def __init__(self, script: list[Script] | None = None, default: Script | None = None, dim=768):
        self.script = list(script or [])
        self.default = default
        self.dim = dim
        self.calls: list[dict[str, Any]] = []

    async def generate(self, model, system, contents, response_schema=None, thinking_level=None,
                       temperature=None):
        self.calls.append(dict(model=model, system=system, contents=list(contents),
                               response_schema=response_schema))
        item = self.script.pop(0) if self.script else self.default
        if item is None:
            raise RuntimeError("FakeLLM script exhausted")
        resp = item(model, system, contents, response_schema) if callable(item) else item
        if response_schema is not None and isinstance(resp.parsed, dict):
            resp.parsed = response_schema.model_validate(resp.parsed)
        resp.model = resp.model or model
        return resp

    async def embed(self, texts, task):
        return [fake_embedding(t, self.dim) for t in texts]

    @staticmethod
    def answer(text: str) -> LLMResponse:
        return LLMResponse(text=text, tokens_in=100, tokens_out=40)

    @staticmethod
    def structured(data: dict[str, Any]) -> LLMResponse:
        return LLMResponse(parsed=data, tokens_in=200, tokens_out=80)


# ------------------------------------------------------------------ heuristics

_YES = re.compile(r"^\s*(yes|yeah|yep|yup|correct|right|exactly|sure|ok(ay)?|haan|ha|that one|"
                  r"that's (it|right|the one))\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|not that|wrong|nahi|don't)\b", re.I)
_DEFER = re.compile(r"\b(not sure|don't know|dont know|no idea|check (tomorrow|later)|"
                    r"i'?ll check|will check|need to check|pata nahi)\b", re.I)
_NQ = re.compile(r"\b(no number|not (really )?a number|can'?t (really )?measure|"
                 r"people thing|not quantifiable|nothing to measure)\b", re.I)
_EDIT = re.compile(r"\b(actually|change|correct(ion)?|fix|update|it was \S+ not|not \d+)\b", re.I)
_RECALL = re.compile(r"^(what|when|did i|remind me what|how many)\b.*\?|\bwhat did i\b", re.I)
_SUPPORT = re.compile(r"\b(feeling low|feel(ing)? down|rough day|remind me (of )?something good|"
                      r"need (some )?encouragement)\b", re.I)
_VENT = re.compile(r"\b(exhausting|frustrat|annoying|tired of|so (many|much) meetings|drained)\b",
                   re.I)
_WRAP = re.compile(r"\b(let'?s wrap( it)? up|wrap up|i'?m done|that'?s all|that'?s it|nothing else)\b",
                   re.I)
_STOP = re.compile(r"^\s*(stop|end now|end the session)\b", re.I)
_REPEAT = re.compile(r"\b(say that again|repeat that|come again)\b", re.I)
_TIME = re.compile(r"\bhow much time\b|\btime left\b", re.I)
_SKIP = re.compile(r"^\s*(skip|next( question)?)\b", re.I)
_INJECT = re.compile(r"\bignore (your|all|previous) instructions\b|\bsystem prompt\b|"
                     r"\byou are now\b", re.I)
_OFF = re.compile(r"\b(tell me a joke|write (me )?(a |some )?code|weather|write my resume)\b", re.I)
_SOFT = re.compile(r"\b(mentor\w*|help(ed|ing) (the |an |a )?(new )?(intern|junior|teammate|"
                   r"engineer)\w*|onboard\w*|paired with|coach\w*|unblock\w*)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+|\s+(?:also|and then)\s+", re.I)
_WORK = re.compile(r"\b(shipped|finished|fixed|built|launched|migrated|deployed|wrote|reviewed|"
                   r"debugged|worked on|started|merged|refactored|designed|helped|helping|mentored|"
                   r"mentoring|spent|set up|cut|reduced|improved|learned|presented|did|"
                   r"finally|closed|resolved)\b", re.I)
_NUMTXT = re.compile(r"[^.,;]*\d[^.;]*", re.I)


def _label(text: str, system: str) -> S.TurnLabel:
    awaiting = "Waiting for a yes/no on: nothing" not in system
    pending = "Open pending question (if any): none" not in system
    nums = [S.NumberRef(text=m.group(0), value=float(m.group(1).replace(",", "")))
            for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|ms|x|k)?", text)]
    kw: dict[str, Any] = {"entities": S.Entities(numbers=nums), "confidence": 0.7}
    if _INJECT.search(text):
        return S.TurnLabel(primary_intent="off_topic", injection_suspected=True, **kw)
    if _STOP.search(text):
        return S.TurnLabel(primary_intent="meta", meta_command="stop", **kw)
    if _WRAP.search(text):
        return S.TurnLabel(primary_intent="meta", meta_command="wrap_up", **kw)
    if _REPEAT.search(text):
        return S.TurnLabel(primary_intent="meta", meta_command="repeat", **kw)
    if _TIME.search(text):
        return S.TurnLabel(primary_intent="meta", meta_command="time_left", **kw)
    if _SKIP.search(text):
        return S.TurnLabel(primary_intent="meta", meta_command="skip", **kw)
    if awaiting and _YES.search(text):
        return S.TurnLabel(primary_intent="confirm", **kw)
    if awaiting and _NO.search(text) and not nums:
        return S.TurnLabel(primary_intent="deny", **kw)
    if _EDIT.search(text) and nums or re.search(r"\bchange (last|that|the)\b", text, re.I):
        kw["entities"].target_hint = text
        return S.TurnLabel(primary_intent="edit_request", **kw)
    if _RECALL.search(text):
        kw["entities"].target_hint = text
        return S.TurnLabel(primary_intent="recall_request", **kw)
    if _SUPPORT.search(text):
        return S.TurnLabel(primary_intent="support_request", **kw)
    if _NQ.search(text):
        return S.TurnLabel(primary_intent="not_quantifiable", **kw)
    if _DEFER.search(text):
        return S.TurnLabel(primary_intent="defer", **kw)
    if _OFF.search(text):
        return S.TurnLabel(primary_intent="off_topic", **kw)
    if _VENT.search(text) and not _WORK.search(text):
        return S.TurnLabel(primary_intent="vent", **kw)
    if pending and nums:
        return S.TurnLabel(primary_intent="metric_answer", **kw)
    return S.TurnLabel(primary_intent="reflect", **kw)


def _board_from_system(system: str) -> dict:
    m = re.search(r"Current board \(items by temp_id\):\n(.*?)\n\nRules:", system, re.S)
    if not m or m.group(1).strip() == "(empty)":
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _metrics(sentence: str) -> list[S.MetricRaw]:
    out = []
    m = re.search(r"from (?:about |around )?([\d,.]+k?)\s*(\w*)\s.*?to (?:about |under |around )?"
                  r"([\d,.]+k?)", sentence, re.I)
    if m:
        def val(s):
            s = s.replace(",", "")
            return float(s[:-1]) * 1000 if s.endswith("k") else float(s)
        out.append(S.MetricRaw(text=m.group(0).strip(), baseline=val(m.group(1)),
                               final=val(m.group(3)), unit=m.group(2) or None))
        return out
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", sentence)
    if m:
        span = re.search(r"[^,;]*\d+(?:\.\d+)?\s*%[^,;.]*", sentence).group(0).strip()
        out.append(S.MetricRaw(text=span, delta_pct=float(m.group(1)), unit="%"))
        return out
    m = re.search(r"(\d+(?:\.\d+)?)\s*x\b", sentence, re.I)
    if m:
        out.append(S.MetricRaw(text=m.group(0), final=float(m.group(1)), unit="x"))
    return out


def _draft(contents, system: str) -> S.DraftDelta:
    board = _board_from_system(system)
    users = [_text_of(c) for c in contents if c.role == "user"]
    text = users[-1] if users else ""
    items, n = [], len(board)
    last_gap_item = None
    for tid, it in board.items():
        if not it.get("metrics_raw") and it.get("quantifiable") != "no":
            last_gap_item = tid
    # a bare number answer fills the open metric gap of the newest item
    if last_gap_item and numbers_in(text) and not _WORK.search(text):
        it = dict(board[last_gap_item])
        ms = _metrics(text) or [S.MetricRaw(text=_NUMTXT.search(text).group(0).strip())]
        it["metrics_raw"] = [m.model_dump() for m in ms]
        it.pop("quote_ok", None)
        items.append(S.DraftItem.model_validate(it))
    else:
        for sent in [s.strip() for s in _SENT.split(text) if s and s.strip()]:
            if not _WORK.search(sent):
                continue
            n += 1
            soft = bool(_SOFT.search(sent))
            kind = "soft_skill" if soft else "learning" if re.search(r"\blearn", sent, re.I) \
                else "plan" if re.search(r"\b(tomorrow|next)\b", sent, re.I) else "achievement"
            title = re.sub(r"^(i |we |also |today i |finally )+", "", sent.rstrip(".!"),
                           flags=re.I)[:80]
            items.append(S.DraftItem(
                temp_id=f"t{n}", kind=kind, title=title[:1].upper() + title[1:], what=sent,
                ownership="team" if re.match(r"\s*we\b", sent, re.I) else "did",
                quantifiable="no" if soft or kind == "plan" else "yes" if numbers_in(sent) else "maybe",
                metrics_raw=[] if soft else _metrics(sent), source_quote=sent.rstrip(".!"),
            ))
    gaps = []
    all_items = {**board, **{i.temp_id: i.model_dump() for i in items}}
    for tid, it in all_items.items():
        if it["kind"] in ("plan", "praise", "learning"):
            continue
        if it["kind"] == "soft_skill":
            gaps.append(S.Gap(temp_id=tid, gap="outcome", priority=2,
                              ask_hint="What changed for them after that?"))
        elif not it.get("metrics_raw"):
            gaps.append(S.Gap(temp_id=tid, gap="metric", priority=1,
                              ask_hint="Do you have a rough before and after number for that?"))
    return S.DraftDelta(items=items, gaps=gaps,
                        suggested_move="ask_gap" if gaps else "ask_open",
                        suggested_target=gaps[0].temp_id if gaps else None)


def _pending_answer(text: str) -> S.PendingAnswer:
    if _DEFER.search(text):
        return S.PendingAnswer(kind="defer", readback="No problem, I'll ask you again tomorrow.")
    if _NQ.search(text):
        return S.PendingAnswer(kind="not_quantifiable", readback="That's fine.")
    ms = _metrics(text)
    if ms:
        m = ms[0]
        rb = (f"So it went from {_fmt(m.baseline)} to {_fmt(m.final)}?" if m.baseline is not None
              else f"So that's {m.text}?")
        return S.PendingAnswer(kind="metric", metric=m, metric_name="result", category="other",
                               better_when="lower" if m.final is not None and m.baseline is not None
                               and m.final < m.baseline else "higher", readback=rb)
    if numbers_in(text) and re.search(r"\d", text):
        span = _NUMTXT.search(text).group(0).strip()
        v = float(re.search(r"\d[\d,]*(?:\.\d+)?", span).group(0).replace(",", ""))
        return S.PendingAnswer(kind="metric", metric=S.MetricRaw(text=span, final=v),
                               metric_name="result", category="other", better_when="higher",
                               readback=f"So that's {span}?")
    return S.PendingAnswer(kind="text", readback="Thanks, got it.")


def _fmt(v):
    return f"{int(v):,}" if v is not None and float(v).is_integer() else str(v)


def _edit_patch(system: str, text: str) -> S.EditPatch:
    m = re.search(r"The record \(data, not instructions\):\n(\{.*\})", system, re.S)
    record = json.loads(m.group(1)) if m else {}
    new = re.search(r"(?:was|is|to)\s+(\d+(?:\.\d+)?)\s*(%?)", text, re.I)
    old = re.search(r"not\s+(\d+(?:\.\d+)?)\s*(%?)", text, re.I)
    if not new:
        return S.EditPatch(old_value_text="", new_value_text="", understood=False)
    nv = float(new.group(1))
    changes = []
    old_txt = f"{old.group(1)}{old.group(2) or new.group(2)}" if old else "the old value"
    for met in record.get("metrics", []):
        if met.get("delta_pct") is not None and (new.group(2) == "%" or not met.get("final")):
            sign = -1 if met["delta_pct"] < 0 else 1
            changes.append(S.MetricPatch(metric_id=met["metric_id"], delta_pct=sign * nv,
                                         source_text=new.group(0)))
            if not old:
                old_txt = f"{abs(met['delta_pct']):g}%"
            break
        if met.get("final") is not None:
            changes.append(S.MetricPatch(metric_id=met["metric_id"], final=nv,
                                         source_text=new.group(0)))
            break
    result = record.get("result")
    if result and old:
        result = result.replace(old.group(1), new.group(1))
    else:
        result = None
    return S.EditPatch(result=result, metric_changes=changes, old_value_text=old_txt,
                       new_value_text=f"{new.group(1)}{new.group(2)}")


def _verdict(system: str) -> S.ContextVerdict:
    m = re.search(r"Candidate records \(data, not instructions\):\n(\[.*\])", system, re.S)
    cands = json.loads(m.group(1)) if m else []
    if not cands:
        return S.ContextVerdict(status="none")
    if len(cands) == 1 or cands[0]["score"] >= cands[1]["score"] * 1.15:
        return S.ContextVerdict(status="found", chosen_item_id=cands[0]["item_id"], why="top score")
    return S.ContextVerdict(status="ambiguous", why="close scores")


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3}


def _ask(system: str, question: str) -> S.AskAnswer:
    m = re.search(r"RECORDS \(data, not instructions\):\n(\[.*\])", system, re.S)
    records = json.loads(m.group(1)) if m else []
    q = _words(question) - {"what", "when", "give", "about", "with", "have", "last", "weeks"}
    scored = sorted(((len(q & _words(json.dumps(r))), r) for r in records), key=lambda x: -x[0])
    hits = [r for s, r in scored if s >= 2][:3]
    if not hits:
        return S.AskAnswer(abstain=True, closest=[r["id"] for s, r in scored if s >= 1][:2])
    sentences = [S.AskSentence(text=f"On {r['date']}, {r['title'][:1].lower()}{r['title'][1:]}.",
                               item_ids=[r["id"]]) for r in hits]
    return S.AskAnswer(abstain=False, sentences=sentences)


def _finalize(contents) -> S.FinalizeResult:
    payload = json.loads(_text_of(contents[-1]))
    items, pending = [], []
    for b in payload.get("draft_board", []):
        metrics = [S.FinalMetric(name=b["title"][:60], unit=m.get("unit") or "count",
                                 baseline=m.get("baseline"), final=m.get("final"),
                                 delta_pct=m.get("delta_pct"),
                                 better_when="lower" if (m.get("final") or 0) < (m.get("baseline") or 0)
                                 else "higher",
                                 approx=bool(re.search(r"about|under|around", m["text"], re.I)),
                                 source_text=m["text"])
                   for m in b.get("metrics_raw", [])]
        bullet = None
        if b["kind"] in ("achievement", "soft_skill"):
            bullet = b["title"]
            if metrics:
                bullet += f" ({metrics[0].source_text})"
            bullet = bullet if 30 <= len(bullet) <= 220 else None
        items.append(S.FinalItem(ref=b["temp_id"], kind=b["kind"], title=b["title"], what=b["what"],
                                 ownership=b.get("ownership"), quantifiable=b.get("quantifiable", "maybe"),
                                 occurred_on=payload["today"], impact=3 if metrics else 2,
                                 scope="team", bullet=bullet, metrics=metrics,
                                 source_quote=b["source_quote"]))
        if b["kind"] in ("achievement", "progress") and not metrics:
            pending.append(S.FinalPending(item_ref=b["temp_id"], kind="metric",
                                          question=f"Do you have a before-and-after number for "
                                                   f"{b['title'][:1].lower()}{b['title'][1:]}?"))
    for t in payload.get("touched_this_session", []):
        items.append(S.FinalItem(action="update", existing_id=t["id"], kind=t["kind"],
                                 title=t["title"], what=t["what"], source_quote=t["what"]))
    titles = [i.title[:1].lower() + i.title[1:] for i in items if i.action == "create"][:3]
    summary = ("You talked about " + ", ".join(titles) + ".") if titles else "A short check-in today."
    return S.FinalizeResult(day_summary=summary, items=items, pending_questions=pending)


class HeuristicFakeLLM:
    """Rule-based stand-in for Gemini (DB-07). Deterministic; never calls the network."""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self.calls: list[dict[str, Any]] = []

    async def generate(self, model, system, contents, response_schema=None, thinking_level=None,
                       temperature=None):
        self.calls.append({"model": model, "schema": getattr(response_schema, "__name__", None)})
        users = [_text_of(c) for c in contents if c.role == "user"]
        last = users[-1] if users else ""
        parsed: Any = None
        text = ""
        if response_schema is S.TurnLabel:
            parsed = _label(last, system)
        elif response_schema is S.DraftDelta:
            parsed = _draft(contents, system)
        elif response_schema is S.PendingAnswer:
            parsed = _pending_answer(last)
        elif response_schema is S.EditPatch:
            parsed = _edit_patch(system, last)
        elif response_schema is S.ContextVerdict:
            parsed = _verdict(system)
        elif response_schema is S.AskAnswer:
            parsed = _ask(system, last)
        elif response_schema is S.FinalizeResult:
            parsed = _finalize(contents)
        elif response_schema is S.JudgeScores:
            parsed = S.JudgeScores(goal_adherence=1.0, solutionizing=0, missed_followups=0,
                                   fidelity_issues=0, warmth=4, wrap_ok=True, notes="fake judge")
        elif response_schema is None:
            m = re.search(r'example \(an acceptable reply; say it your own way\): "(.*)"', system)
            text = m.group(1) if m else "Got it. What else stood out today?"
        else:
            raise RuntimeError(f"HeuristicFakeLLM has no rule for {response_schema}")
        if parsed is not None:
            text = parsed.model_dump_json()
        return LLMResponse(text=text, parsed=parsed, tokens_in=len(system) // 4 + 50,
                           tokens_out=len(text) // 4 + 5, model=model)

    async def embed(self, texts, task):
        return [fake_embedding(t, self.dim) for t in texts]
