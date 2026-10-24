"""Deterministic safety pre-check (plan 14.4). Runs before every LLM; on a match the turn takes the
fixed template path: no LLM call, no memory item, and the trace records only that it ran.

The phrase list is deliberately broad (English + common Hinglish). False positives cost one gentle
message; false negatives are worse.
"""

from __future__ import annotations

import re

HELPLINE = {
    "helpline_name": "Tele-MANAS",
    "helpline_number": "14416",
    "tel": "tel:14416",
}

MESSAGE = (
    "I'm really glad you told me. I'm not the right help for this, but you deserve support right "
    "now. Please reach out to someone you trust, or call Tele-MANAS at 1 4 4 1 6, free, any time."
)

DISPLAY_MESSAGE = (
    "I'm really glad you told me. Daybook isn't the right help for this, but you deserve support "
    "right now. Please reach out to someone you trust, or call Tele-MANAS on 14416 (free, 24×7, India)."
)

_PATTERNS = [
    r"\bkill (my ?self|myself)\b", r"\bend (it all|my life)\b", r"\bsuicid", r"\bself[- ]?harm",
    r"\bhurt(ing)? myself\b", r"\bwant to die\b", r"\bwanna die\b", r"\bbetter off dead\b",
    r"\bno reason to live\b", r"\bdon'?t want to (live|be alive|wake up)\b",
    r"\bdon'?t see the point of (anything|living|life|going on)\b",
    r"\bcan'?t go on\b", r"\bnot worth living\b", r"\btake my (own )?life\b",
    r"\bdisappear forever\b", r"\bnobody would (miss|care)\b",
    # Hinglish
    r"\bmar(na)? jaana? chahta\b", r"\bmarna chahti\b", r"\bmar jaun\b", r"\bjeene ka mann nahi\b",
    r"\bjeena nahi chahta\b", r"\bjeena nahi chahti\b", r"\bkhud ko (khatam|hurt)\b",
    r"\bsuicide kar\b", r"\bzindagi khatam\b",
]
_RX = re.compile("|".join(_PATTERNS), re.IGNORECASE)

# Mild synthetic phrases used by tests and the manual test J1. Never real user text.
TEST_PHRASES = [
    "I don't see the point of anything anymore",
    "sometimes I feel like nobody would miss me",
    "jeene ka mann nahi karta yaar",
]


def is_crisis(text: str) -> bool:
    return bool(_RX.search(text))


def safety_payload() -> dict:
    return {**HELPLINE, "message": DISPLAY_MESSAGE}
