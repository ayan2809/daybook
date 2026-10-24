"""Agent contracts (plan 6.3). Field names are binding.

Schemas passed to Gemini as `response_schema` avoid free-form dicts (Gemini's schema subset handles
named fields, enums, lists and optionals well). Code-only shapes may use anything.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "reflect", "metric_answer", "defer", "not_quantifiable", "edit_request", "confirm", "deny",
    "recall_request", "support_request", "vent", "meta", "off_topic", "crisis_signal",
]
MetaCommand = Literal["wrap_up", "stop", "pause", "repeat", "skip", "time_left"]
Kind = Literal[
    "achievement", "progress", "learning", "challenge", "soft_skill", "decision", "blocker",
    "praise", "plan",
]
Ownership = Literal["led", "did", "helped", "team"]
Quantifiable = Literal["yes", "maybe", "no"]
GapKind = Literal["metric", "outcome", "ownership", "how", "why_it_mattered"]
Move = Literal[
    "ask_gap", "ask_open", "ask_hard_part", "ask_learning", "ask_tomorrow", "acknowledge", "none"
]
MetricCategory = Literal[
    "latency", "throughput", "cost", "revenue", "reliability", "time", "scale", "quality",
    "adoption", "other",
]
Scope = Literal["individual", "team", "org", "company", "customers", "public"]
Period = Literal["one-time", "per-day", "per-week", "per-month", "per-year"]


# ------------------------------------------------------------------ Researcher → TurnLabel


class ProjectRef(BaseModel):
    text: str
    matched_project_id: int | None = None


class TimeRef(BaseModel):
    text: str
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None


class NumberRef(BaseModel):
    text: str  # verbatim
    value: float | None = None
    unit: str | None = None


class Entities(BaseModel):
    projects: list[ProjectRef] = Field(default_factory=list)
    time_refs: list[TimeRef] = Field(default_factory=list)
    numbers: list[NumberRef] = Field(default_factory=list)
    target_hint: str | None = None


class TurnLabel(BaseModel):
    primary_intent: Intent = "reflect"
    secondary_intents: list[Intent] = Field(default_factory=list)
    meta_command: MetaCommand | None = None
    pending_question_id: int | None = None
    entities: Entities = Field(default_factory=Entities)
    injection_suspected: bool = False
    confidence: float = 0.5


# ------------------------------------------------------------------ Analyzer fast pass → DraftDelta


class MetricRaw(BaseModel):
    text: str
    baseline: float | None = None
    final: float | None = None
    delta_pct: float | None = None
    unit: str | None = None


class DraftItem(BaseModel):
    temp_id: str
    kind: Kind
    title: str
    what: str
    how: str | None = None
    result: str | None = None
    ownership: Ownership | None = None
    project_hint: str | None = None
    quantifiable: Quantifiable = "maybe"
    metrics_raw: list[MetricRaw] = Field(default_factory=list)
    source_quote: str


class Gap(BaseModel):
    temp_id: str
    gap: GapKind
    priority: int = 2  # 1 (most valuable) – 3
    ask_hint: str = ""


class DraftDelta(BaseModel):
    items: list[DraftItem] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    suggested_move: Move = "none"
    suggested_target: str | None = None
    energy: int | None = None
    mood_word: str | None = None


# ------------------------------------------------------------------ edit patch (fast pass, edit flow)


class MetricPatch(BaseModel):
    metric_id: int | None = None  # existing metric to change; None adds a new one
    name: str | None = None
    unit: str | None = None
    baseline: float | None = None
    final: float | None = None
    delta_pct: float | None = None
    source_text: str  # the user's exact words with the new number


class EditPatch(BaseModel):
    title: str | None = None
    what: str | None = None
    how: str | None = None
    result: str | None = None
    occurred_on: str | None = None
    metric_changes: list[MetricPatch] = Field(default_factory=list)
    old_value_text: str  # e.g. "50%"
    new_value_text: str  # e.g. "40%"
    understood: bool = True  # False when the request doesn't say what to change


# ------------------------------------------------------------------ pending answer parse


class PendingAnswer(BaseModel):
    kind: Literal["metric", "not_quantifiable", "defer", "text"]
    metric: MetricRaw | None = None
    metric_name: str | None = None
    category: MetricCategory | None = None
    better_when: Literal["higher", "lower"] | None = None
    readback: str  # "So p99 went from 120 to 45 ms?"


# ------------------------------------------------------------------ Context agent


class ContextCandidate(BaseModel):
    item_id: int
    date: str
    title: str
    field_values: dict = Field(default_factory=dict)
    score: float = 0.0


class ContextResult(BaseModel):
    status: Literal["found", "ambiguous", "none"]
    candidates: list[ContextCandidate] = Field(default_factory=list)
    chosen_item_id: int | None = None
    why: str = ""


class ContextVerdict(BaseModel):
    """What the Flash-Lite verifier returns (LLM-facing)."""

    status: Literal["found", "ambiguous", "none"]
    chosen_item_id: int | None = None
    why: str = ""


# ------------------------------------------------------------------ Analyzer deep pass → FinalizeResult


class FinalMetric(BaseModel):
    name: str
    category: MetricCategory = "other"
    unit: str
    baseline: float | None = None
    final: float | None = None
    delta_pct: float | None = None  # only when the user stated a percentage and nothing else
    better_when: Literal["higher", "lower"] = "higher"
    period: Period | None = None
    approx: bool = False
    source_text: str


class ProjectChoice(BaseModel):
    existing_id: int | None = None
    new_name: str | None = None


class FinalItem(BaseModel):
    action: Literal["create", "update", "merge_into"] = "create"
    existing_id: int | None = None  # for update / merge_into
    ref: str = ""  # a short id for this item within the result (used by links and pending)
    kind: Kind
    title: str
    what: str
    how: str | None = None
    result: str | None = None
    ownership: Ownership | None = None
    project: ProjectChoice | None = None
    occurred_on: str | None = None
    quantifiable: Quantifiable = "maybe"
    scope: Scope | None = None
    impact: int | None = None
    bullet: str | None = None
    metrics: list[FinalMetric] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    source_quote: str
    source_turn_seqs: list[int] = Field(default_factory=list)


class FinalPending(BaseModel):
    item_ref: str  # a FinalItem.ref, or "id:<existing item id>"
    kind: Literal["metric", "outcome", "ownership", "date", "clarify"]
    question: str


class FinalResolved(BaseModel):
    pending_id: int
    resolution: Literal["answered", "not_quantifiable", "deferred", "dismissed"]
    answer_text: str = ""


class FinalLink(BaseModel):
    from_ref: str
    to_item_id: int
    kind: Literal["continues", "same_as", "caused", "blocked_by"]


class FinalizeResult(BaseModel):
    day_summary: str
    energy: int | None = None
    mood_word: str | None = None
    items: list[FinalItem] = Field(default_factory=list)
    pending_questions: list[FinalPending] = Field(default_factory=list)
    resolved_pending: list[FinalResolved] = Field(default_factory=list)
    links: list[FinalLink] = Field(default_factory=list)


# ------------------------------------------------------------------ Ask agent


class AskSentence(BaseModel):
    text: str
    item_ids: list[int] = Field(default_factory=list)


class AskAnswer(BaseModel):
    abstain: bool
    sentences: list[AskSentence] = Field(default_factory=list)
    closest: list[int] = Field(default_factory=list)


# ------------------------------------------------------------------ session judge


class JudgeScores(BaseModel):
    goal_adherence: float  # 0–1
    solutionizing: int
    missed_followups: int
    fidelity_issues: int
    warmth: int  # 1–5
    wrap_ok: bool
    notes: str = ""
