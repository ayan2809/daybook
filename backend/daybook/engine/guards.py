"""Inline guards on every Chat reply (plan 7.6). Pure code, ~0 ms.

`check()` returns {rule: passed}. `no_markdown` never fails: it strips instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from daybook.memory.validate import numbers_unsupported

ADVICE = (
    "you should",
    "try to",
    "i'd recommend",
    "i would recommend",
    "have you considered",
    "you might want to",
    "you could try",
    "my advice",
    "make sure you",
    "consider ",
)
ASKED_FOR_ADVICE = (
    "should i",
    "any advice",
    "what do you think",
    "what would you do",
    "any tips",
    "suggest",
    "recommend",
)
FORWARD = (
    "tomorrow",
    "next time",
    "next session",
    "later",
    "ask you",
    "i'll ask",
    "check back",
    "come back",
    "follow up",
)
_GENERIC = {
    "numbers",
    "number",
    "what",
    "changed",
    "after",
    "part",
    "how",
    "came",
    "together",
    "why",
    "mattered",
    "your",
    "the",
}
MISSED = ("miss", "anything else", "leave out", "left out", "forget anything", "forgot")
_MD = re.compile(r"(^\s*[-*•]\s+|^\s*\d+\.\s+|^#+\s*|\*\*|__|`|https?://\S+)", re.M)
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿️]")
_STOP = {"the", "a", "an", "for", "of", "to", "and", "your", "on", "in", "with", "you", "my"}


@dataclass
class GuardContext:
    sources: list[str]  # the user's turns, the operator note facts, retrieved records
    last_user_text: str = ""
    max_words: int = 45
    wrap: bool = False
    promised: list[str] = field(default_factory=list)  # short topics the wrap must name


def strip_markdown(text: str) -> str:
    text = _MD.sub("", text)
    text = _EMOJI.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", s.lower())


def check(reply: str, ctx: GuardContext) -> dict[str, bool]:
    low = reply.lower()
    res = {
        "one_question": reply.count("?") <= 1,
        "length": len(reply.split()) <= ctx.max_words,
        "no_advice": not any(a in low for a in ADVICE)
        or any(a in ctx.last_user_text.lower() for a in ASKED_FOR_ADVICE),
        "fidelity": not numbers_unsupported(reply, ctx.sources),
    }
    if ctx.wrap:
        # each promised question must be named by its own words (not the generic gap words), and
        # the reply must say it's coming back to them; a plain read-back doesn't count
        named = all(
            any(w in low for w in _words(topic) if w not in _STOP | _GENERIC and len(w) > 2)
            for topic in ctx.promised
        )
        forward = not ctx.promised or any(c in low for c in FORWARD)
        res["phase"] = any(m in low for m in MISSED) and named and forward
    return res


def failures(results: dict[str, bool]) -> list[str]:
    return [k for k, ok in results.items() if not ok]


CORRECTIONS = {
    "one_question": "Ask at most ONE question.",
    "length": "Keep it shorter: under {max_words} words.",
    "no_advice": "Don't give advice or suggestions.",
    "fidelity": "Only use numbers that appear in the user's words or the operator note. Don't add any.",
    "phase": "Read back what you captured, name each open question you'll ask tomorrow, and ask "
    "whether you missed anything.",
}


def correction_note(failed: list[str], ctx: GuardContext) -> str:
    return " ".join(CORRECTIONS[f].format(max_words=ctx.max_words) for f in failed)
