# Daybook HTTP API (binding contract)

Backend: FastAPI at `/api` (local: `http://localhost:8300`; Vite proxies `/api` → `:8300`).
Frontend and backend both follow this file. Change it only together with both sides, and note the change in
`docs/DECISIONS.md`.

## Conventions

- JSON in and out unless noted. Dates are `YYYY-MM-DD` (user's timezone, default Asia/Kolkata);
  timestamps are ISO 8601 UTC strings.
- Auth is a cookie (`db_session`, HttpOnly, `Path=/api`). The frontend sends requests with
  `credentials: "same-origin"` and never reads the cookie.
- **Sealed vaults (DB-32):** an account signed in but not yet unlocked gets **423 `vault_locked`** from every data route; only `/api/me`, `/api/auth/*` and `/api/vault/*` work until it sends its recovery key.
- **Private by default (DB-31):** every `/api/*` request needs a valid signed session cookie (account or demo) and gets 401 otherwise, before any route runs. The only public routes are `GET /api/health`, `GET /api/me`, `POST /api/auth/login`, `POST /api/auth/google`, `POST /api/auth/logout` and `POST /api/demo/start` (3 a minute per IP, 100 new sandboxes a day; 429 `rate_limited` / `demo_cap`). `/api/docs` and `/api/openapi.json` exist only outside production.
- **Every mutating request (POST/PUT/PATCH/DELETE) must send `X-Requested-With: daybook`**, or it gets
  403. Exception: `POST /api/sessions/{id}/end` (so `navigator.sendBeacon` works).
- Errors always look like `{"error": {"code": "<code>", "message": "<human text>", ...extra}}`.

| HTTP | code | When |
|---|---|---|
| 401 | `unauthorized` | no or bad cookie |
| 403 | `forbidden` | wrong role (demo on owner route), missing `X-Requested-With`, origin check |
| 404 | `not_found` | |
| 409 | `session_active` | starting a session while one is active; extra: `session_id` |
| 409 | `time_up` | a turn after the time limit; the session has ended, go to the summary |
| 409 | `session_ended` | a turn on an ended session |
| 422 | `validation` | bad body |
| 429 | `rate_limited` · `demo_cap` · `budget` | limits; `demo_cap` means use replay (`GET /api/demo/replay`); `budget` on `POST /api/sessions` means a `user` account hit its daily cap |
| 500/503 | `internal` · `unavailable` | |

- **Time travel (dev and demo only):** header `X-Debug-Now: 2026-10-27T19:05:00+05:30` overrides "now".
  The frontend sends it when `localStorage["daybook.debugNow"]` is set (wrap access in try/catch).

## Shared types (TypeScript notation)

```ts
type Role = "owner" | "demo";
type Kind = "achievement" | "progress" | "learning" | "challenge" | "soft_skill" | "decision" | "blocker" | "praise" | "plan";
type Phase = "open" | "close_loops" | "explore" | "dump" | "followups" | "capture" | "edit" | "wrap" | "done";
type Mode = "guided" | "dump" | "quick" | "edit";

interface Pending {
  id: number; item_id: number | null; item_title: string | null;
  kind: "metric" | "outcome" | "ownership" | "date" | "clarify";
  question: string; status: "open" | "answered" | "not_quantifiable" | "dismissed" | "expired";
  times_asked: number; created_at: string; snooze_until: string | null;
}

interface Metric {
  id: number; name: string; category: string; unit: string;
  baseline: number | null; final: number | null; delta_abs: number | null; delta_pct: number | null;
  better_when: "higher" | "lower"; period: string | null; approx: boolean; is_derived: boolean;
  source_text: string;
  display: string;            // ready to show, e.g. "120 → 45 ms (−62.5%)"
}

interface ItemRef { id: number; title: string; occurred_on: string; kind: Kind }

interface MemoryItem {
  id: number; kind: Kind; status: "draft" | "active" | "hidden" | "merged";
  occurred_on: string; project: { id: number; name: string } | null;
  title: string; what: string; how: string | null; result: string | null; bullet: string | null;
  scope: string | null; impact: number | null; quantifiable: "yes" | "maybe" | "no";
  ownership: "led" | "did" | "helped" | "team" | null;
  source_quote: string;       // the user's exact words ("You said: …")
  metrics: Metric[]; skills: string[]; version: number; updated_at: string;
  validation_note: string | null; open_pending: Pending[];
}

interface BoardItem {
  temp_id: string; kind: Kind; title: string;
  chip: "number_captured" | "needs_number" | "soft_skill" | "needs_outcome" | "captured";
  metrics_text: string[];     // e.g. ["1,200 → under 100 failed deliveries a day"]
}
interface Board { items: BoardItem[] }

interface Turn {
  seq: number; role: "user" | "assistant"; text: string; created_at: string;
  turn_id: string | null;     // assistant turns only: use for audio
  safety: boolean;            // true on the fixed safety reply
}

interface PendingAction { kind: "confirm_edit" | "confirm_metric" | "pick_item" | "describe_edit"; prompt: string }

interface SafetyInfo { helpline_name: string; helpline_number: string; tel: string /* "tel:14416" */; message: string }

interface SessionView {
  id: string; mode: Mode; status: "active" | "ended" | "finalizing" | "finalized" | "failed";
  phase: Phase; limit_sec: number; started_at: string; seconds_left: number;
  voice_enabled: boolean;     // false once the voice budget or TTS failures turned voice off
  turns: Turn[]; board: Board; pending_action: PendingAction | null;
  suggestions: string[];      // demo only: suggested reply chips (empty otherwise)
}

interface Settings {
  reminder: { enabled: boolean; time: string; days: number[]; second_nudge: boolean; second_time: string }; // days: 1=Mon … 7=Sun
  session_limit_sec: 300 | 360 | 420;
  voice: boolean; auto_send: boolean; pending_at: "start" | "end"; keep_names: boolean;
  profile: { display_name: string | null; org: string | null; role_title: string | null; timezone: string };
}
```

## Clarifications (2026-10-25)

- Push payload (sent by the server, read by `sw.ts`): `{title, body, url}`.
- Exports, push and data delete are for any signed-in account (`owner` or `user`), never the demo; Admin is owner-only. Demo users can `GET`/`PUT /api/settings`; a demo's
  `reminder` changes are ignored.
- `include_hidden=true` on `GET /api/memories` includes hidden items.
- There's no read-aloud endpoint for Ask or Remind me yet (P1).

## Endpoints

### Health, auth, demo

| Method + path | Body | 200 response |
|---|---|---|
| `GET /api/health` (`?warm=1` also loads the vault) | | `{ok: true, env, llm, tts}` |
| `POST /api/auth/login` | `{owner_key}` | `{role: "owner", vault: "new" \| "locked"}` (sets a locked cookie) · 401 on a bad key |
| `POST /api/auth/google` | `{credential}` (the ID token from Google Identity Services) | `{role: "owner" \| "user", vault: "new" \| "locked"}` (sets a locked cookie; the vault opens with `/api/vault/*`). The first sign-in creates the account (DB-29); an email in `OWNER_EMAILS` opens the owner's data. 401 bad token or unverified email · 403 `not_invited` (allowlist) · 403 `signups_closed` (`MAX_ACCOUNTS`) · 503 not configured |
| `POST /api/auth/logout` | | `{ok: true}` |
| `GET /api/me` | | `{role: Role \| null, settings: Settings \| null, demo: {sandbox_id, sessions_left, asks_left, replay_only: boolean} \| null, features: {voice: boolean, push: boolean, vapid_public_key: string \| null, google_client_id: string \| null}, account: {email: string \| null, vault: "new" \| "locked" \| "open"} \| null}`. While an account's vault isn't open, `settings` is `null`. `Role` is `"owner" \| "user" \| "demo"`. Signed out → `role: null` (200, not 401). |
| `POST /api/vault/setup` | `{recovery_key}` (made in the browser: 32 base32 characters, dashes and case ignored) | `{vault: "open"}` (re-sets the cookie with the vault key inside, encrypted). 409 if the account already has a key · 422 malformed key. DB-32 |
| `POST /api/vault/unlock` | `{recovery_key}` | `{vault: "open"}` · 403 `wrong_key` (deliberately not 401) · 409 `vault_new` |
| `POST /api/vault/reset` | `{confirm: "start over"}` | `{vault: "new"}`. A lost key: forget it and start an empty vault; the old data stays sealed and unreachable |
| `POST /api/demo/start` | | `{role: "demo", sandbox_id}` (sets cookie; new sandbox seeded with the demo persona) |
| `GET /api/demo/replay` | | `{banner: string, turns: Turn[] (with `audio_url: string \| null`), summary: Summary}` |
| `GET /api/demo/last-trace` | | the sandbox's last turn trace (same shape as an admin trace turn) or `null` |

### Today and sessions

| Method + path | Body | 200 response |
|---|---|---|
| `GET /api/today` | | `{date, weekday, display_name: string \| null, reflected_today: boolean, active_session_id: string \| null, streak: {date, reflected}[] (14, oldest first), pending: Pending[], last_summary: {date, text, session_id} \| null}` |
| `POST /api/sessions` | `{mode: Mode, item_id?: number}` (`item_id` only for `edit`) | 201 `{session: SessionView}`; the opener is the last assistant turn. 409 `session_active` with `session_id`. 429 `demo_cap`. |
| `GET /api/sessions/{id}` | | `{session: SessionView}` (use to restore after reload) |
| `POST /api/sessions/{id}/turns` | `{client_turn_id: string (uuid), text: string (1–4000 chars), dictated_chars: number}` | `TurnResult` (below). Idempotent on `client_turn_id`: a retry returns the same result. |
| `GET /api/turns/{turn_id}/audio` | | the reply audio: `audio/wav` (Gemini voice) or `audio/mpeg` (ElevenLabs). **204** when voice is off for the session or out of budget; **503** when TTS failed (show "voice unavailable"). |
| `POST /api/sessions/{id}/end` | `{reason: "user" \| "idle"}` (beacon: text/plain body is fine) | 202 `{status}`. `user` ends the session now. `idle` (send it on `pagehide`) is a soft signal: the server ends the session only if no turn arrives within 2 minutes (DB-13). Ending an already-ended session returns 202 with its status; an unknown id is 404. |
| `GET /api/sessions/{id}/summary` | | `Summary` (below). Poll every 1.5 s until `status` is `finalized` or `failed`. |
| `POST /api/sessions/{id}/feedback` | `{value: "up" \| "down"}` | `{ok: true}` |

```ts
interface TurnResult {
  turn_id: string; reply_text: string; phase: Phase; seconds_left: number;
  board: Board; pending_action: PendingAction | null; ended: boolean;
  voice: boolean;             // audio is available for this reply
  safety: SafetyInfo | null;  // non-null → show the helpline card with tap-to-call and a "pause" button
  suggestions: string[];
}
interface Summary {
  session_id: string; status: "ended" | "finalizing" | "finalized" | "failed"; date: string;
  day_summary: string | null; items: MemoryItem[]; pending_created: Pending[];
  stats: { duration_sec: number; turns: number; spoken_pct: number | null /* 0–100 */ }; feedback: "up" | "down" | null;
}
```

### Memories (Timeline)

| Method + path | Body | 200 response |
|---|---|---|
| `GET /api/memories?from=&to=&project=&kind=&q=&include_hidden=` | | `{items: MemoryItem[], projects: {id, name}[]}` newest first |
| `GET /api/memories/{id}` | | `{item: MemoryItem, versions: {version, changed_by, reason, created_at, snapshot: object}[]}` newest first |
| `PATCH /api/memories/{id}` | `{fields: {title?, what?, how?, result?, bullet?, kind?, ownership?, occurred_on?}, reason?: string}` | `{item}` (new version) |
| `POST /api/memories/{id}/hide` · `/unhide` | | `{item}` |
| `POST /api/memories/{id}/restore` | `{version}` | `{item}` (a new version with the old content) |

### Pending questions

| Method + path | Body | 200 response |
|---|---|---|
| `GET /api/pending` | | `{pending: Pending[]}` (open ones) |
| `POST /api/pending/{id}/answer` | `{text}` | `{candidate: {kind: "metric" \| "not_quantifiable" \| "defer" \| "text", readback: string, metric: object \| null, text: string}}`: nothing saved yet |
| `POST /api/pending/{id}/confirm` | `{candidate}` (as returned) | `{pending: Pending, item: MemoryItem \| null}` |
| `POST /api/pending/{id}/snooze` · `/not-quantifiable` · `/dismiss` | | `{pending: Pending}` |

### Ask and Remind me

| Method + path | Body | 200 response |
|---|---|---|
| `POST /api/ask` | `{question}` | `{answer: string, sentences: {text, item_ids: number[]}[], citations: {item_id, sentence}[], abstained: boolean, closest: ItemRef[], refs: Record<string, ItemRef>}` · 429 `budget` / `demo_cap` |
| `GET /api/remind-me` | | `{cards: {item_id, kind, title, occurred_on, text}[], empty_message: string \| null}` |

### Settings, push, export, data

| Method + path | Body | 200 response |
|---|---|---|
| `GET /api/settings` | | `Settings` |
| `PUT /api/settings` | partial `Settings` (deep-merged) | `Settings` |
| `POST /api/push/subscribe` | the browser's `PushSubscription.toJSON()`: `{endpoint, keys: {p256dh, auth}}` | `{ok: true}` (accounts only) |
| `DELETE /api/push/subscribe` | `{endpoint}` | `{ok: true}` |
| `POST /api/push/test` | | `{sent: number}` |
| `GET /api/export/resume-as-code?since=` | | JSON array of Resume-as-code notes (accounts only) |
| `GET /api/export/markdown` | | `text/markdown` file |
| `GET /api/export/brain` | | `text/markdown` file `daybook-memory.md`: the whole memory written for an LLM to read (DB-33). Accounts and the demo |
| `GET /api/export/sqlite` | | `application/x-sqlite3` file |
| `POST /api/data/delete` | `{confirm: "delete"}` | `{ok: true}` (accounts only; deletes everything, a Google account's record and its reminder schedules too, and signs out) |

### Admin (owner only)

| Method + path | 200 response |
|---|---|
| `GET /api/admin/overview?days=30` | `{days, sessions, completed, completion_rate, median_duration_sec, time_limit_hits, cost_total_usd, cost_per_session_usd, latency: {turn_p50_ms, turn_p95_ms, by_span: Record<string, {p50_ms, p95_ms}>}, tts_chars, goal_adherence_avg: number \| null, guard_fail_rate: Record<string, number>, fidelity_issues, metric_capture_rate: number \| null, pending_resolution_rate: number \| null, ask_abstain_rate: number \| null, feedback: {up, down}, spend_today: {total, demo, owner, budget}}` |
| `GET /api/admin/sessions` | `{sessions: {id, mode, local_date, started_at, duration_sec, turn_count, status, end_reason, cost_usd, latency_p50_ms, guard_fails, judge: object \| null, flagged: boolean}[]}` |
| `GET /api/admin/sessions/{id}/trace` | `{session: {...the row above}, turns: Trace[], jobs: Trace[]}`; `Trace` is the per-turn JSON in plan §13.1 (`seq, user_text, reply_text, totals, spans[]`) |
| `GET /api/admin/evals` | `{runs: {id, created_at, mode, suites: Record<string, {score, gate, passed}>}[]}` |
