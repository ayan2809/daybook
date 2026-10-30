"""Text normalization for speech (plan 9.2): "p99" → "p ninety-nine", "ms" → "milliseconds"."""

from __future__ import annotations

import re

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def say_int(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" {say_int(r)}" if r else "")
    return str(n)


_UNITS = [
    (r"(?<=\d)\s?ms\b", " milliseconds"),
    (r"(?<=\d)\s?s\b(?!\w)", " seconds"),
    (r"(?<=\d)\s?req/s\b", " requests per second"),
    (r"(?<=\d)\s?rps\b", " requests per second"),
    (r"(?<=\d)\s?qps\b", " queries per second"),
    (r"(?<=\d)\s?GB\b", " gigabytes"),
    (r"(?<=\d)\s?MB\b", " megabytes"),
    (r"(?<=\d)\s?k\b", " thousand"),
    (r"(?<=\d)\s?x\b", " times"),
    (r"(?<=\d)\s?%", " percent"),
]


def for_speech(text: str) -> str:
    t = text
    t = re.sub(
        r"\bp(50|90|95|99|999)\b",
        lambda m: "p " + (say_int(int(m.group(1))) if m.group(1) != "999" else "nine nine nine"),
        t,
        flags=re.I,
    )
    t = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)", r"\1 rupees", t)
    t = re.sub(r"\$\s?([\d,]+(?:\.\d+)?)", r"\1 dollars", t)
    t = re.sub(r"(\d)\s?→\s?(\d)", r"\1 to \2", t)
    t = t.replace("→", " to ")
    for pat, rep in _UNITS:
        t = re.sub(pat, rep, t, flags=re.I if pat.endswith("x\\b") else 0)
    t = re.sub(r"\bvs\.?\b", "versus", t)
    t = re.sub(r"\be\.g\.", "for example", t)
    return re.sub(r"\s+", " ", t).strip()
