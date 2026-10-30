"""Structured logging: one JSON line per event. Never log user text (plan 13.1)."""

from __future__ import annotations

import json
import logging
import sys

_log = logging.getLogger("daybook")


def setup_logging(level: int = logging.INFO) -> None:
    if not _log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(message)s"))
        _log.addHandler(h)
    _log.setLevel(level)
    _log.propagate = False


def log_event(**fields) -> None:
    _log.info(json.dumps(fields, default=str, separators=(",", ":")))
