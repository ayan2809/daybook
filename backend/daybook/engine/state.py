"""The short-term memory: one session file per session, rewritten each turn (plan 11).

`users/{uid}/sessions/{sid}.json` (owner) or `demo/{sandbox}/sessions/{sid}.json` (demo).
Saved with If-Match so two tabs can't silently overwrite each other.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from daybook.storage.blob import BlobStore


class TurnRec(BaseModel):
    seq: int
    role: str  # user | assistant
    text: str
    created_at: str
    intent: str | None = None
    dictated_chars: int | None = None
    turn_id: str | None = None
    safety: bool = False
    move: str | None = None


class PendingAsk(BaseModel):
    id: int
    question: str
    kind: str
    item_id: int | None = None
    item_title: str | None = None


class PendingActionState(BaseModel):
    # confirm_metric | confirm_edit | pick_item | describe_edit | find_item | confirm_item
    kind: str
    prompt: str
    data: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    id: str
    uid: str
    role: str  # owner | demo
    mode: str
    local_date: str
    tz: str
    started_at: str  # ISO with offset
    limit_sec: int
    phase: str = "open"
    status: str = "active"  # active | ended | finalizing | finalized | failed
    end_reason: str | None = None
    ended_at: str | None = None
    turns: list[TurnRec] = Field(default_factory=list)
    board_items: dict[str, dict] = Field(default_factory=dict)  # temp_id → DraftItem + checks
    gaps: list[dict] = Field(default_factory=list)
    pending_queue: list[PendingAsk] = Field(default_factory=list)
    current_pending: PendingAsk | None = None
    asked_pending: list[int] = Field(default_factory=list)
    resolved_pending: list[dict] = Field(default_factory=list)
    touched_items: list[int] = Field(default_factory=list)
    followups: dict[str, int] = Field(default_factory=dict)
    open_prompts_done: list[str] = Field(default_factory=list)
    followups_asked: int = 0  # brain dump: at most 3
    pending_action: PendingActionState | None = None
    wrap_turns: int = 0
    promised_questions: list[dict] = Field(default_factory=list)
    budgets: dict[str, float] = Field(
        default_factory=lambda: {"tts_chars": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    )
    voice_enabled: bool = True
    tts_failures: int = 0
    tts_char_limit: int = 3000
    client_turns: dict[str, dict] = Field(default_factory=dict)
    energy: int | None = None
    mood_word: str | None = None
    edit_item_id: int | None = None
    opener_context: dict[str, Any] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    turn_latencies: list[int] = Field(default_factory=list)
    guard_fails: dict[str, int] = Field(default_factory=dict)
    flags: dict[str, Any] = Field(default_factory=dict)
    finalize_result: dict | None = None
    summary: dict | None = None
    feedback: str | None = None

    # ------------------------------------------------------------------ helpers

    def started(self) -> datetime:
        return datetime.fromisoformat(self.started_at)

    def elapsed(self, now: datetime) -> float:
        return (now - self.started()).total_seconds()

    def seconds_left(self, now: datetime) -> int:
        return max(0, int(round(self.limit_sec - self.elapsed(now))))

    def user_texts(self) -> list[str]:
        return [t.text for t in self.turns if t.role == "user" and not t.safety]

    def last_assistant(self) -> str | None:
        for t in reversed(self.turns):
            if t.role == "assistant":
                return t.text
        return None

    def next_seq(self) -> int:
        return (self.turns[-1].seq + 1) if self.turns else 1

    def turn_dicts(self) -> list[dict]:
        return [t.model_dump() for t in self.turns]


def session_key(prefix: str, sid: str) -> str:
    return f"{prefix}sessions/{sid}.json"


def load(store: BlobStore, prefix: str, sid: str) -> tuple[SessionState, str] | None:
    blob = store.get(session_key(prefix, sid))
    if blob is None:
        return None
    return SessionState.model_validate_json(blob.data), blob.etag


def save(store: BlobStore, prefix: str, state: SessionState, etag: str | None) -> str:
    data = json.dumps(state.model_dump(), ensure_ascii=False).encode()
    key = session_key(prefix, state.id)
    if etag is None:
        return store.put(key, data, if_none_match=True, content_type="application/json")
    return store.put(key, data, if_match=etag, content_type="application/json")
