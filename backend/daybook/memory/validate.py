"""Code-only checks and arithmetic (plan 8.2, 6.2): quotes must be the user's words, numbers must
appear in the user's words, and deltas are computed here, never by an LLM.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_WS = re.compile(r"\s+")
_EDGE_PUNCT = " \t\n.,;:!?\"'“”‘’()[]-–—"

WORD_NUMBERS = {
    "zero": 0, "none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "half": 50, "double": 2,
    "twice": 2, "triple": 3, "a dozen": 12, "dozen": 12,
}
_NUM = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|mn|million|lakh|lakhs|crore|cr|x|%)?",
    re.IGNORECASE,
)
_MULT = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "lakh": 1e5, "lakhs": 1e5,
         "crore": 1e7, "cr": 1e7}


def norm_text(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return _WS.sub(" ", s).strip().lower()


def quote_in(quote: str | None, sources: list[str]) -> bool:
    """True when `quote` is a span of one of `sources` (or of all of them joined), after
    whitespace, case and edge-punctuation normalization."""
    if not quote:
        return False
    q = norm_text(quote).strip(_EDGE_PUNCT)
    if len(q) < 3:
        return False
    hay = [norm_text(s) for s in sources]
    return any(q in h for h in hay) or q in " ".join(hay)


def numbers_in(text: str) -> set[float]:
    """Every number a person could mean in `text`: digits (with commas, k/m/lakh multipliers, %
    and x) plus common number words. Both the raw and the multiplied value are included."""
    out: set[float] = set()
    for m in _NUM.finditer(text):
        raw = float(m.group(1).replace(",", ""))
        out.add(raw)
        suffix = (m.group(2) or "").lower()
        if suffix in _MULT:
            out.add(raw * _MULT[suffix])
        if suffix == "x":
            out.add(1.0)  # "4x" implies a baseline of 1
    low = text.lower()
    for word, val in WORD_NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            out.add(float(val))
    return out


def number_supported(value: float | None, sources: list[str], rel_tol: float = 0.005) -> bool:
    if value is None:
        return True
    pool: set[float] = set()
    for s in sources:
        pool |= numbers_in(s)
    v = abs(value)
    return any(math.isclose(v, p, rel_tol=rel_tol, abs_tol=1e-9) for p in pool)


def numbers_unsupported(text: str, sources: list[str]) -> list[str]:
    """Numbers written in `text` (digits only) that don't appear in `sources`."""
    pool: set[float] = set()
    for s in sources:
        pool |= numbers_in(s)
    bad = []
    for m in _NUM.finditer(text):
        raw = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        candidates = {raw, raw * _MULT.get(suffix, 1)}
        if not any(math.isclose(c, p, rel_tol=0.005) for c in candidates for p in pool):
            bad.append(m.group(0).strip())
    return bad


# ------------------------------------------------------------------ deltas


_DOWN_WORDS = ("drop", "reduc", "cut", "fell", "fall", "faster", "lower", "less", "fewer",
               "decreas", "down", "saved", "shrank", "shrunk", "quicker")
_UP_WORDS = ("increas", "grew", "grow", "up ", "more", "higher", "faster", "improv", "boost",
             "rose", "gain")


@dataclass
class Deltas:
    baseline: float | None
    final: float | None
    delta_abs: float | None
    delta_pct: float | None
    is_derived: bool


def compute_deltas(
    baseline: float | None,
    final: float | None,
    delta_pct: float | None,
    unit: str | None = None,
    better_when: str = "higher",
    source_text: str = "",
) -> Deltas:
    """Plan A3: 120 → 45 gives delta_abs −75, delta_pct −62.5 (derived). A stated "dropped 35%"
    keeps only delta_pct −35. "4x" gives baseline 1, final 4."""
    if unit and unit.lower() in ("x", "times") and final is not None and baseline is None:
        baseline = 1.0
    if baseline is not None and final is not None:
        d_abs = round(final - baseline, 6)
        d_pct = round((final - baseline) / baseline * 100, 2) if baseline != 0 else None
        return Deltas(baseline, final, d_abs, d_pct, True)
    if delta_pct is not None:
        delta_pct = _signed_pct(delta_pct, better_when, source_text)
    return Deltas(baseline, final, None, delta_pct, False)


def _signed_pct(pct: float, better_when: str, source_text: str) -> float:
    """Fix the sign of a stated percentage from the direction words around it."""
    low = source_text.lower()
    mag = abs(pct)
    if better_when == "lower" and any(w in low for w in _DOWN_WORDS):
        return -mag
    if better_when == "higher" and any(w in low for w in _UP_WORDS):
        return mag
    if any(w in low for w in ("drop", "reduc", "cut", "fell", "decreas", "fewer", "less")):
        return -mag
    return pct


def fmt_num(v: float | None) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def metric_display(m: dict) -> str:
    """'120 → 45 ms (−62.5%)', '−35%', '4x'."""
    unit = m.get("unit") or ""
    u = "" if unit in ("%", "count", "") else f" {unit}"
    approx = "~" if m.get("approx") else ""
    pct = m.get("delta_pct")
    pct_s = f"{'+' if pct > 0 else '−' if pct < 0 else ''}{fmt_num(abs(pct))}%" if pct is not None else ""
    b, f = m.get("baseline"), m.get("final")
    if unit.lower() in ("x", "times") and f is not None and (b in (None, 1, 1.0)):
        return f"{approx}{fmt_num(f)}x"
    if b is not None and f is not None:
        s = f"{approx}{fmt_num(b)} → {fmt_num(f)}{u}"
        return f"{s} ({pct_s})" if pct_s else s
    if f is not None:
        return f"{approx}{fmt_num(f)}{u}" + (f" ({pct_s})" if pct_s else "")
    if pct_s:
        return f"{approx}{pct_s}"
    return m.get("source_text", "")


def bullet_ok(bullet: str | None) -> bool:
    return bullet is None or 30 <= len(bullet) <= 220
