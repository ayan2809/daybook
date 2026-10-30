"""Phases and the time budget (plan 7.1). The server clock is the one that counts.

guided: open → close_loops (≤ 21% of the limit, max 90 s, ≤ 3 questions; skipped when nothing is
        pending) → explore → wrap (the last 14%, or on "let's wrap up") → done
dump:   dump → followups (≤ 3) → wrap → done
quick:  capture → done
edit:   edit → done
"""

from __future__ import annotations

from datetime import datetime

from daybook.engine.state import SessionState

GRACE_SEC = 15  # a turn after limit + 15 s gets 409 time_up
MAX_PENDING_PER_SESSION = 3
MAX_FOLLOWUPS_PER_ITEM = 2
MAX_DUMP_FOLLOWUPS = 3


def close_loops_cap(limit: int) -> int:
    return int(min(90, max(30, round(0.21 * limit))))


def wrap_window(limit: int) -> int:
    return int(max(25, round(0.14 * limit)))


def wrap_due(state: SessionState, now: datetime) -> bool:
    return state.elapsed(now) >= state.limit_sec - wrap_window(state.limit_sec)


def over_limit(state: SessionState, now: datetime) -> bool:
    return state.elapsed(now) >= state.limit_sec


def past_grace(state: SessionState, now: datetime) -> bool:
    return state.elapsed(now) > state.limit_sec + GRACE_SEC


def close_loops_open(state: SessionState, now: datetime) -> bool:
    """Still allowed to ask pending questions at the start."""
    return (
        bool(state.pending_queue)
        and len(state.asked_pending) < MAX_PENDING_PER_SESSION
        and state.elapsed(now) < close_loops_cap(state.limit_sec)
    )
