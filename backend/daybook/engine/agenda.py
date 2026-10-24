"""Choosing the next move (plan 7.3) and building the operator note for the Chat agent.

`decide()` applies the rules in order and returns a `Step`: either a ready `Move`, or a request for
the conductor to do async work first (retrieval, parsing an answer, applying a confirmed change).
Every Move carries a safe `template`: the guard fallback, and what the fake LLM says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from daybook.agents.schemas import TurnLabel
from daybook.engine import phases
from daybook.engine.state import PendingAsk, SessionState

OPEN_PROMPTS = [
    ("hard_part", "What was the hardest part of today?"),
    ("learning", "Did you learn anything new today?"),
    ("tomorrow", "What's the one thing you want to get done tomorrow?"),
    ("what_else", "What else stood out today?"),
]
GAP_TEMPLATES = {
    "metric": "Do you have a rough number for that, like a before and after?",
    "outcome": "What changed because of it?",
    "ownership": "What was your part in it?",
    "how": "How did you go about it?",
    "why_it_mattered": "Why did that one matter?",
}
SOFT_KINDS = ("soft_skill",)
RULES = "one question at most; under 40 words; no advice; don't invent facts; no lists or markdown"


@dataclass
class Move:
    name: str
    template: str
    instruction: str = ""
    about: str | None = None
    target: str | None = None
    gap: str | None = None
    ask_hint: str | None = None
    pending_id: int | None = None
    say_back: list[str] = field(default_factory=list)
    ack: str | None = None
    max_words: int = 45
    wrap: bool = False
    promised: list[str] = field(default_factory=list)
    end: bool = False
    use_llm: bool = True  # False: say the template verbatim (safety, repeat, fixed confirmations)

    def note(self, state: SessionState, now: datetime) -> str:
        lines = [f"move: {self.name}      phase: {state.phase}      "
                 f"seconds_left: {state.seconds_left(now)}"]
        if self.ack:
            lines.append(f"first: {self.ack}")
        if self.about:
            miss = f" — missing: {self.gap}" if self.gap else ""
            tgt = f" (item {self.target})" if self.target else ""
            lines.append(f'about: "{self.about}"{tgt}{miss}')
        if self.ask_hint:
            lines.append(f"ask_hint: {self.ask_hint}")
        if self.say_back:
            lines.append("say_back_exactly: " + json.dumps(self.say_back, ensure_ascii=False))
        if self.promised:
            lines.append("open_questions_for_tomorrow (name each): "
                         + json.dumps(self.promised, ensure_ascii=False))
        if self.instruction:
            lines.append(f"instruction: {self.instruction}")
        lines.append(f'example (an acceptable reply; say it your own way): "{self.template}"')
        words = 40 if self.max_words <= 45 else self.max_words - 5
        lines.append(f"rules: {RULES.replace('under 40', f'under {words}')}")
        return "\n".join(lines)


@dataclass
class Step:
    kind: str  # move | safety | edit_search | recall_search | pending_answer | confirm | deny |
    #            pick | describe_edit | find_again
    move: Move | None = None


# ------------------------------------------------------------------ move builders


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def _count_word(n: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(n, str(n))


def opener(state: SessionState, name: str | None, plan_text: str | None) -> Move:
    hi = f"Hi {name}." if name else "Hi."
    if state.mode == "dump":
        return Move("open_dump", f"{hi} Go ahead and tell me everything about today, in one go. "
                    "I'm listening.", instruction="Invite a brain dump. No question about details.")
    if state.mode == "quick":
        return Move("open_quick", f"{hi} What's the one thing you got done today?")
    if state.pending_queue:
        n = min(len(state.pending_queue), phases.MAX_PENDING_PER_SESSION)
        first = state.pending_queue[0]
        ones = "one" if n == 1 else "ones"
        return Move(
            "open_pending",
            f"{hi} Before we start, {_count_word(n)} quick {ones} from last time. {first.question}",
            instruction="Greet briefly, say you have a few quick questions from last time, then ask "
                        "the first one exactly as given.",
            say_back=[first.question], pending_id=first.id, max_words=55,
        )
    if plan_text:
        return Move(
            "open_plan", f"{hi} Last time you planned to {_lower_first(plan_text)}. How did it go?",
            instruction="Greet briefly and ask how yesterday's plan went.",
            say_back=[plan_text],
        )
    return Move("open", f"{hi} How did today go? What stood out?",
                instruction="Greet briefly; ask for one word on energy or what stood out today.")


def ask_pending(p: PendingAsk, ack: str | None = None, ack_template: str = "") -> Move:
    t = f"{ack_template} {p.question}".strip()
    return Move("ask_pending", t, ack=ack, say_back=[p.question], pending_id=p.id,
                about=p.item_title, instruction="Ask this open question from last time, as given.",
                max_words=55)


def _sorted_gaps(state: SessionState, numbers_first: bool = False) -> list[dict]:
    gaps = [g for g in state.gaps if g.get("temp_id") in state.board_items]
    gaps = [g for g in gaps if state.followups.get(g["temp_id"], 0) < phases.MAX_FOLLOWUPS_PER_ITEM]
    gaps = [g for g in gaps if g["temp_id"] not in state.flags.get("skipped", [])]
    if numbers_first:
        return sorted(gaps, key=lambda g: (g["gap"] != "metric", g.get("priority", 2)))
    return sorted(gaps, key=lambda g: g.get("priority", 2))


def ask_gap(state: SessionState, g: dict, ack: str | None = None, ack_template: str = "") -> Move:
    item = state.board_items[g["temp_id"]]
    gap = g["gap"]
    if item.get("kind") in SOFT_KINDS and gap == "metric":
        gap = "outcome"
    hint = (g.get("ask_hint") or "").strip()
    q = hint if hint.endswith("?") and len(hint.split()) <= 25 else GAP_TEMPLATES[gap]
    instr = ("Mirror their words briefly, then ask about the missing piece. ")
    if item.get("kind") in SOFT_KINDS:
        instr += "This is people work: ask who it helped or what changed, never 'by how much'. "
    if gap == "metric":
        instr += "Say a rough number is fine, and that not knowing is fine too."
    return Move("ask_gap", f"{ack_template} {q}".strip(), instruction=instr,
                about=_lower_first(item["title"]), target=g["temp_id"], gap=gap, ask_hint=hint or None,
                ack=ack)


def ask_open(state: SessionState, key: str, text: str, ack: str | None = None,
             ack_template: str = "Got it.") -> Move:
    return Move(f"ask_{key}" if key != "what_else" else "ask_open",
                f"{ack_template} {text}".strip(), ack=ack,
                instruction="Mirror what they just said in a few words, then ask this open question.")


def wrap(state: SessionState) -> Move:
    items = sorted(
        state.board_items.values(),
        key=lambda i: (i.get("kind") not in ("achievement", "soft_skill", "challenge"),),
    )[:3]
    titles = [_lower_first(i["title"]) for i in items]
    promised = []
    seen = set()
    for g in sorted(state.gaps, key=lambda g: g.get("priority", 2)):
        if g.get("priority", 2) > 2 or g["temp_id"] not in state.board_items or g["temp_id"] in seen:
            continue
        item = state.board_items[g["temp_id"]]
        gap = "outcome" if item.get("kind") in SOFT_KINDS and g["gap"] == "metric" else g["gap"]
        topic = {"metric": "the numbers for", "outcome": "what changed after",
                 "ownership": "your part in", "how": "how you did",
                 "why_it_mattered": "why it mattered:"}.get(gap, "")
        promised.append({"temp_id": g["temp_id"], "gap": gap, "title": item["title"],
                         "about": f"{topic} {_lower_first(item['title'])}".strip(),
                         "ask_hint": g.get("ask_hint")})
        seen.add(g["temp_id"])
        if len(promised) == 3:
            break
    state.promised_questions = promised
    captured = _join(titles) if titles else "not much yet, and that's okay"
    t = f"Here's what I captured: {captured}."
    if promised:
        t += f" Tomorrow I'll ask you about {_join([p['about'] for p in promised])}."
    t += " Did I miss anything?"
    return Move(
        "wrap", t, wrap=True, max_words=80, say_back=titles,
        promised=[p["about"] for p in promised],
        instruction="Briefly read back what you captured (the say_back items), name each open "
                    "question for tomorrow, then ask if you missed anything.",
    )


def goodbye(state: SessionState, reason: str = "user", added: bool = False) -> Move:
    if state.mode == "quick":
        t = "Got it, saved. I'll ask about the details tomorrow."
    elif reason == "time_limit":
        t = "That's our time for today. Thanks, everything's saved. Have a good evening."
    elif reason == "stop":
        t = "Okay, stopping here. Everything so far is saved."
    elif added:
        t = "Got it, I've added that. Thanks, everything's saved. Have a good evening."
    else:
        t = "Thanks, that's saved. Have a good evening."
    return Move("goodbye", t, end=True, use_llm=reason not in ("stop",),
                instruction="Say a short, warm goodbye. No question.")


def _join(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def next_move(state: SessionState, now: datetime, ack: str | None = None,
              ack_template: str = "") -> Move:
    """Rules 6–7: the phase's move, else the top gap, else an open prompt, else wrap."""
    if state.mode == "quick":
        return goodbye(state)
    if phases.over_limit(state, now):
        return goodbye(state, "time_limit")
    if state.phase == "wrap":
        return goodbye(state, added=bool(state.flags.get("wrap_added")))
    if state.mode in ("guided", "dump") and phases.wrap_due(state, now):
        return wrap(state)
    if state.mode == "guided" and state.phase in ("open", "close_loops"):
        remaining = [p for p in state.pending_queue if p.id not in state.asked_pending]
        if remaining and phases.close_loops_open(state, now):
            return ask_pending(remaining[0], ack, ack_template)
    if state.mode == "dump":
        if state.followups_asked < phases.MAX_DUMP_FOLLOWUPS:
            gaps = _sorted_gaps(state, numbers_first=True)
            if gaps:
                return ask_gap(state, gaps[0], ack, ack_template)
        return wrap(state)
    gaps = _sorted_gaps(state)
    if gaps:
        return ask_gap(state, gaps[0], ack, ack_template or "")
    for key, text in OPEN_PROMPTS:
        if key not in state.open_prompts_done:
            return ask_open(state, key, text, ack, ack_template or "Got it.")
    return wrap(state)


# ------------------------------------------------------------------ the rules (plan 7.3)


def decide(state: SessionState, label: TurnLabel, now: datetime) -> Step:
    intent = label.primary_intent
    pa = state.pending_action
    meta = label.meta_command if intent == "meta" else None

    # 1. a pending action waiting for an answer
    if pa and meta not in ("stop", "wrap_up"):
        if pa.kind in ("confirm_metric", "confirm_edit", "confirm_item"):
            if intent == "confirm":
                return Step("confirm")
            if intent == "deny":
                return Step("deny")
            if pa.kind == "confirm_metric" and intent in ("metric_answer", "reflect"):
                return Step("pending_answer")  # they restated the number
            if pa.kind == "confirm_item" and intent in ("reflect", "edit_request", "recall_request"):
                return Step("pick")  # "no, the other one" style answers
        elif pa.kind == "pick_item":
            return Step("deny") if intent == "deny" else Step("pick")
        elif pa.kind == "describe_edit":
            return Step("deny") if intent == "deny" else Step("describe_edit")
        elif pa.kind == "find_item":
            return Step("deny") if intent == "deny" else Step("find_again")

    # 2. safety, support, venting
    if intent == "crisis_signal":
        return Step("safety")
    if intent == "support_request":
        return Step("move", Move(
            "support",
            "That sounds like a heavy day. When you want it, the Remind me page shows hard things "
            "you've gotten through. What's on your mind?",
            instruction="Acknowledge the feeling first, warmly. Offer the Remind me page. Never "
                        "diagnose or give advice. Not therapy.",
            max_words=50,
        ))
    if intent == "vent":
        return Step("move", Move(
            "reflect_vent", "That sounds really draining.",
            instruction="Reflect their feeling back in their words. No advice. At most one gentle "
                        "question, or none.",
        ))

    # 3. meta commands
    if meta == "stop":
        return Step("move", goodbye(state, "stop"))
    if meta == "wrap_up":
        if state.phase == "wrap" or state.mode in ("quick", "edit"):
            return Step("move", goodbye(state))
        return Step("move", wrap(state))
    if meta == "repeat":
        last = state.last_assistant() or "I didn't say anything yet."
        return Step("move", Move("repeat", last, use_llm=False, max_words=90))
    if meta == "time_left":
        mins = max(1, round(state.seconds_left(now) / 60))
        return Step("move", Move("time_left", f"About {mins} minute{'s' if mins > 1 else ''} left.",
                                 use_llm=False))
    if meta == "pause":
        return Step("move", Move("pause", "Sure, take your time. I'm here when you're ready.",
                                 use_llm=False))
    if meta == "skip":
        last_target = state.flags.get("last_target")
        if last_target:
            state.flags.setdefault("skipped", []).append(last_target)
        if state.current_pending:
            state.flags.setdefault("skipped_pending", []).append(state.current_pending.id)
            state.current_pending = None
        return Step("move", next_move(state, now, ack_template="Sure, let's skip that."))

    # 4. edits and recall
    if intent == "edit_request":
        return Step("edit_search")
    if intent == "recall_request":
        return Step("recall_search")

    # 5. answers to the pending question being asked
    if state.current_pending and intent not in ("off_topic",):
        return Step("pending_answer")

    if intent == "off_topic" or label.injection_suspected:
        return Step("move", Move(
            "steer_back", "I'm here for your reflection. What else happened at work today?",
            instruction="Kindly say you're here for their reflection, and steer back to their day.",
        ))

    # 6–7. phase move, gaps, open prompts
    if state.phase == "wrap":
        return Step("move", goodbye(state, added=bool(state.flags.get("wrap_added"))))
    return Step("move", next_move(state, now))
