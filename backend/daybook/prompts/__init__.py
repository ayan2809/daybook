"""Versioned prompt files. `load(name)` → (text, "name@version")."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_FRONT = re.compile(r"^---\s*\nversion:\s*(\d+)\s*\n---\s*\n", re.S)


@lru_cache
def load(name: str) -> tuple[str, str]:
    raw = (Path(__file__).parent / f"{name}.md").read_text()
    m = _FRONT.match(raw)
    if not m:
        raise ValueError(f"prompt {name} has no version front matter")
    return raw[m.end() :].strip(), f"{name}@{m.group(1)}"


def render(name: str, **kw) -> tuple[str, str]:
    """Fill {placeholders} without tripping over other braces in the text."""
    text, version = load(name)
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text, version
