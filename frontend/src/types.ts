// Types from docs/API.md (the binding contract). Keep in sync with that file.

// owner: Ayan (admin pages) · user: any other Google account, own private data · demo: sandbox
export type Role = "owner" | "user" | "demo";
export type Kind =
  | "achievement"
  | "progress"
  | "learning"
  | "challenge"
  | "soft_skill"
  | "decision"
  | "blocker"
  | "praise"
  | "plan";
export type Phase =
  | "open"
  | "close_loops"
  | "explore"
  | "dump"
  | "followups"
  | "capture"
  | "edit"
  | "wrap"
  | "done";
export type Mode = "guided" | "dump" | "quick" | "edit";

export const KINDS: Kind[] = [
  "achievement",
  "progress",
  "learning",
  "challenge",
  "soft_skill",
  "decision",
  "blocker",
  "praise",
  "plan",
];

export interface Pending {
  id: number;
  item_id: number | null;
  item_title: string | null;
  kind: "metric" | "outcome" | "ownership" | "date" | "clarify";
  question: string;
  status: "open" | "answered" | "not_quantifiable" | "dismissed" | "expired";
  times_asked: number;
  created_at: string;
  snooze_until: string | null;
}

export interface Metric {
  id: number;
  name: string;
  category: string;
  unit: string;
  baseline: number | null;
  final: number | null;
  delta_abs: number | null;
  delta_pct: number | null;
  better_when: "higher" | "lower";
  period: string | null;
  approx: boolean;
  is_derived: boolean;
  source_text: string;
  display: string;
}

export interface ItemRef {
  id: number;
  title: string;
  occurred_on: string;
  kind: Kind;
}

export type Ownership = "led" | "did" | "helped" | "team";

export interface MemoryItem {
  id: number;
  kind: Kind;
  status: "draft" | "active" | "hidden" | "merged";
  occurred_on: string;
  project: { id: number; name: string } | null;
  title: string;
  what: string;
  how: string | null;
  result: string | null;
  bullet: string | null;
  scope: string | null;
  impact: number | null;
  quantifiable: "yes" | "maybe" | "no";
  ownership: Ownership | null;
  source_quote: string;
  metrics: Metric[];
  skills: string[];
  version: number;
  updated_at: string;
  validation_note: string | null;
  open_pending: Pending[];
}

export type BoardChip = "number_captured" | "needs_number" | "soft_skill" | "needs_outcome" | "captured";

export interface BoardItem {
  temp_id: string;
  kind: Kind;
  title: string;
  chip: BoardChip;
  metrics_text: string[];
}
export interface Board {
  items: BoardItem[];
}

export interface Turn {
  seq: number;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  turn_id: string | null;
  safety: boolean;
}

export interface PendingAction {
  kind: "confirm_edit" | "confirm_metric" | "pick_item" | "describe_edit";
  prompt: string;
}

export interface SafetyInfo {
  helpline_name: string;
  helpline_number: string;
  tel: string;
  message: string;
}

export type SessionStatus = "active" | "ended" | "finalizing" | "finalized" | "failed";

export interface SessionView {
  id: string;
  mode: Mode;
  status: SessionStatus;
  phase: Phase;
  limit_sec: number;
  started_at: string;
  seconds_left: number;
  voice_enabled: boolean;
  turns: Turn[];
  board: Board;
  pending_action: PendingAction | null;
  suggestions: string[];
}

export interface ReminderSettings {
  enabled: boolean;
  time: string;
  days: number[]; // 1=Mon … 7=Sun
  second_nudge: boolean;
  second_time: string;
}

export interface Profile {
  display_name: string | null;
  org: string | null;
  role_title: string | null;
  timezone: string;
}

export interface Settings {
  reminder: ReminderSettings;
  session_limit_sec: 300 | 360 | 420;
  voice: boolean;
  auto_send: boolean;
  pending_at: "start" | "end";
  keep_names: boolean;
  profile: Profile;
}

/** Partial Settings for PUT /api/settings (deep-merged by the server). */
export interface SettingsPatch {
  reminder?: Partial<ReminderSettings>;
  session_limit_sec?: Settings["session_limit_sec"];
  voice?: boolean;
  auto_send?: boolean;
  pending_at?: Settings["pending_at"];
  keep_names?: boolean;
  profile?: Partial<Profile>;
}

// ---- Health, auth, demo ----

export interface Health {
  ok: true;
  env: string;
  llm: string;
  tts: string;
}

export interface DemoInfo {
  sandbox_id: string;
  sessions_left: number;
  asks_left: number;
  replay_only: boolean;
}

export interface Features {
  voice: boolean;
  push: boolean;
  vapid_public_key: string | null;
  google_client_id: string | null;
}

export interface Me {
  role: Role | null;
  settings: Settings | null;
  demo: DemoInfo | null;
  features: Features;
  account: { email: string | null } | null;
}

export interface ReplayTurn extends Turn {
  audio_url: string | null;
}

export interface DemoReplay {
  banner: string;
  turns: ReplayTurn[];
  summary: Summary;
}

// ---- Today and sessions ----

export interface Today {
  date: string;
  weekday: string;
  display_name: string | null;
  reflected_today: boolean;
  active_session_id: string | null;
  streak: { date: string; reflected: boolean }[];
  pending: Pending[];
  last_summary: { date: string; text: string; session_id: string } | null;
}

export interface TurnResult {
  turn_id: string;
  reply_text: string;
  phase: Phase;
  seconds_left: number;
  board: Board;
  pending_action: PendingAction | null;
  ended: boolean;
  voice: boolean;
  safety: SafetyInfo | null;
  suggestions: string[];
}

export interface Summary {
  session_id: string;
  status: "ended" | "finalizing" | "finalized" | "failed";
  date: string;
  day_summary: string | null;
  items: MemoryItem[];
  pending_created: Pending[];
  stats: { duration_sec: number; turns: number; spoken_pct: number | null };
  feedback: "up" | "down" | null;
}

// ---- Memories ----

export interface MemoryFilters {
  from?: string;
  to?: string;
  project?: number | string;
  kind?: Kind;
  q?: string;
  include_hidden?: boolean;
}

export interface MemoryList {
  items: MemoryItem[];
  projects: { id: number; name: string }[];
}

export interface MemoryVersion {
  version: number;
  changed_by: string;
  reason: string | null;
  created_at: string;
  snapshot: Record<string, unknown>;
}

export interface MemoryDetail {
  item: MemoryItem;
  versions: MemoryVersion[];
}

export interface MemoryPatchFields {
  title?: string;
  what?: string;
  how?: string;
  result?: string;
  bullet?: string;
  kind?: Kind;
  ownership?: Ownership;
  occurred_on?: string;
}

// ---- Pending ----

export interface PendingCandidate {
  kind: "metric" | "not_quantifiable" | "defer" | "text";
  readback: string;
  metric: Record<string, unknown> | null;
  text: string;
}

// ---- Ask and Remind me ----

export interface AskResult {
  answer: string;
  sentences: { text: string; item_ids: number[] }[];
  citations: { item_id: number; sentence: string }[];
  abstained: boolean;
  closest: ItemRef[];
  refs: Record<string, ItemRef>;
}

export interface RemindCard {
  item_id: number;
  kind: Kind;
  title: string;
  occurred_on: string;
  text: string;
}

export interface RemindMe {
  cards: RemindCard[];
  empty_message: string | null;
}

// ---- Push ----

export interface PushSubscriptionBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

// ---- Admin ----

export interface AdminOverview {
  days: number;
  sessions: number;
  completed: number;
  completion_rate: number;
  median_duration_sec: number;
  time_limit_hits: number;
  cost_total_usd: number;
  cost_per_session_usd: number;
  latency: {
    turn_p50_ms: number;
    turn_p95_ms: number;
    by_span: Record<string, { p50_ms: number; p95_ms: number }>;
  };
  tts_chars: number;
  goal_adherence_avg: number | null;
  guard_fail_rate: Record<string, number>;
  fidelity_issues: number;
  metric_capture_rate: number | null;
  pending_resolution_rate: number | null;
  ask_abstain_rate: number | null;
  feedback: { up: number; down: number };
  spend_today: { total: number; demo: number; owner: number; user: number; budget: number };
}

export interface AdminSessionRow {
  id: string;
  mode: Mode;
  local_date: string;
  started_at: string;
  duration_sec: number;
  turn_count: number;
  status: string;
  end_reason: string | null;
  cost_usd: number;
  latency_p50_ms: number;
  guard_fails: number;
  judge: Record<string, unknown> | null;
  flagged: boolean;
}

/** One span of a trace (plan §13.1). Attributes are open-ended. */
export interface TraceSpan {
  span_id?: string;
  parent_id?: string | null;
  name: string;
  kind?: string;
  start?: string;
  latency_ms?: number;
  status?: string;
  attrs?: Record<string, unknown>;
}

export interface Trace {
  session_id?: string;
  seq?: number;
  name?: string;
  user_text?: string;
  reply_text?: string;
  totals?: {
    latency_ms?: number;
    cost_usd?: number;
    input_tokens?: number;
    output_tokens?: number;
  };
  spans: TraceSpan[];
}

export interface AdminTrace {
  session: AdminSessionRow;
  turns: Trace[];
  jobs: Trace[];
}

export interface EvalRun {
  id: string;
  created_at: string;
  mode: string;
  suites: Record<string, { score: number; gate: number; passed: boolean }>;
}
