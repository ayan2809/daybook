// Typed fetch wrapper for every endpoint in docs/API.md.
import { getItem, KEYS } from "./lib/storage";
import type {
  VaultState,
  AdminOverview,
  AdminSessionRow,
  AdminTrace,
  AskResult,
  DemoReplay,
  EvalRun,
  Health,
  Me,
  MemoryDetail,
  MemoryFilters,
  MemoryItem,
  MemoryList,
  MemoryPatchFields,
  Mode,
  Pending,
  PendingCandidate,
  PushSubscriptionBody,
  RemindMe,
  SessionView,
  Settings,
  SettingsPatch,
  Summary,
  Today,
  Trace,
  TurnResult,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly extra: Record<string, unknown>;

  constructor(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

/** True for failures where the request never got an HTTP answer (offline, DNS, aborted connection). */
export function isNetworkError(err: unknown): boolean {
  return err instanceof TypeError || (err instanceof ApiError && err.code === "network");
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "You're offline or the server can't be reached.";
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;
/** Called on any 401 so the app can refresh `/api/me` and send the user to the landing page. */
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null): void {
  onUnauthorized = fn;
}

let onLocked: (() => void) | null = null;
export function setLockedHandler(fn: (() => void) | null): void {
  onLocked = fn;
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function buildHeaders(method: string, hasBody: boolean, accept: string): Headers {
  const h = new Headers({ Accept: accept });
  if (hasBody) h.set("Content-Type", "application/json");
  if (MUTATING.has(method)) h.set("X-Requested-With", "daybook");
  const debugNow = getItem(KEYS.debugNow);
  if (debugNow) h.set("X-Debug-Now", debugNow);
  return h;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = res.status === 401 ? "unauthorized" : "internal";
  let message = res.statusText || `Request failed (${res.status})`;
  let extra: Record<string, unknown> = {};
  try {
    const body = (await res.json()) as { error?: Record<string, unknown> };
    if (body && typeof body.error === "object" && body.error) {
      const { code: c, message: m, ...rest } = body.error;
      if (typeof c === "string") code = c;
      if (typeof m === "string") message = m;
      extra = rest;
    }
  } catch {
    /* non-JSON error body: keep the defaults */
  }
  return new ApiError(res.status, code, message, extra);
}

/** Low-level request. Returns the raw Response for 2xx; throws ApiError otherwise. */
export async function rawRequest(
  method: string,
  path: string,
  body?: unknown,
  init: RequestInit = {},
  accept = "application/json",
): Promise<Response> {
  const hasBody = body !== undefined;
  const res = await fetch(path, {
    ...init,
    method,
    credentials: "same-origin",
    headers: buildHeaders(method, hasBody, accept),
    body: hasBody ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await parseError(res);
    if (res.status === 401 && onUnauthorized && !/^\/api\/(auth|vault)\//.test(path)) onUnauthorized();
    if (res.status === 423 && onLocked) onLocked(); // the vault needs its key again
    throw err;
  }
  return res;
}

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const res = await rawRequest(method, path, body, init);
  if (res.status === 204) return null as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const enc = encodeURIComponent;

// ---------------------------------------------------------------------------
// Health, auth, demo

export const api = {
  health: (warm = false) => request<Health>("GET", `/api/health${warm ? "?warm=1" : ""}`),
  login: (owner_key: string) =>
    request<{ role: "owner"; vault: VaultState }>("POST", "/api/auth/login", { owner_key }),
  /** Google sign-in; the first sign-in creates the account. */
  loginGoogle: (credential: string) =>
    request<{ role: "owner" | "user"; vault: VaultState }>("POST", "/api/auth/google", { credential }),
  /** The sealed vault (DB-32): the browser makes the recovery key; the server never stores it. */
  vaultSetup: (recovery_key: string) => request<{ vault: "open" }>("POST", "/api/vault/setup", { recovery_key }),
  vaultUnlock: (recovery_key: string) =>
    request<{ vault: "open" }>("POST", "/api/vault/unlock", { recovery_key }),
  vaultReset: () => request<{ vault: "new" }>("POST", "/api/vault/reset", { confirm: "start over" }),
  logout: () => request<{ ok: true }>("POST", "/api/auth/logout"),
  me: () => request<Me>("GET", "/api/me"),
  demoStart: () => request<{ role: "demo"; sandbox_id: string }>("POST", "/api/demo/start"),
  demoReplay: () => request<DemoReplay>("GET", "/api/demo/replay"),
  demoLastTrace: () => request<Trace | null>("GET", "/api/demo/last-trace"),

  // -------------------------------------------------------------------------
  // Today and sessions
  today: () => request<Today>("GET", "/api/today"),
  startSession: (mode: Mode, item_id?: number) =>
    request<{ session: SessionView }>(
      "POST",
      "/api/sessions",
      mode === "edit" && item_id != null ? { mode, item_id } : { mode },
    ),
  getSession: (id: string) => request<{ session: SessionView }>("GET", `/api/sessions/${enc(id)}`),
  sendTurn: (id: string, body: { client_turn_id: string; text: string; dictated_chars: number }) =>
    request<TurnResult>("POST", `/api/sessions/${enc(id)}/turns`, body),
  endSession: (id: string, reason: "user" | "idle" = "user") =>
    request<{ status: string }>("POST", `/api/sessions/${enc(id)}/end`, { reason }),
  summary: (id: string) => request<Summary>("GET", `/api/sessions/${enc(id)}/summary`),
  feedback: (id: string, value: "up" | "down") =>
    request<{ ok: true }>("POST", `/api/sessions/${enc(id)}/feedback`, { value }),

  /**
   * The reply audio. Resolves to null on 204 (voice off / out of budget).
   * Throws ApiError (503 `unavailable`) when TTS failed.
   */
  turnAudio: async (turnId: string, signal?: AbortSignal): Promise<Blob | null> => {
    const res = await rawRequest("GET", `/api/turns/${enc(turnId)}/audio`, undefined, { signal }, "audio/*");
    if (res.status === 204) return null;
    return res.blob();
  },

  // -------------------------------------------------------------------------
  // Memories
  memories: (f: MemoryFilters = {}) =>
    request<MemoryList>(
      "GET",
      `/api/memories${qs({
        from: f.from,
        to: f.to,
        project: f.project,
        kind: f.kind,
        q: f.q,
        include_hidden: f.include_hidden ? "true" : undefined,
      })}`,
    ),
  memory: (id: number) => request<MemoryDetail>("GET", `/api/memories/${id}`),
  patchMemory: (id: number, fields: MemoryPatchFields, reason?: string) =>
    request<{ item: MemoryItem }>("PATCH", `/api/memories/${id}`, reason ? { fields, reason } : { fields }),
  hideMemory: (id: number) => request<{ item: MemoryItem }>("POST", `/api/memories/${id}/hide`),
  unhideMemory: (id: number) => request<{ item: MemoryItem }>("POST", `/api/memories/${id}/unhide`),
  restoreMemory: (id: number, version: number) =>
    request<{ item: MemoryItem }>("POST", `/api/memories/${id}/restore`, { version }),

  // -------------------------------------------------------------------------
  // Pending questions
  pending: () => request<{ pending: Pending[] }>("GET", "/api/pending"),
  answerPending: (id: number, text: string) =>
    request<{ candidate: PendingCandidate }>("POST", `/api/pending/${id}/answer`, { text }),
  confirmPending: (id: number, candidate: PendingCandidate) =>
    request<{ pending: Pending; item: MemoryItem | null }>("POST", `/api/pending/${id}/confirm`, {
      candidate,
    }),
  snoozePending: (id: number) => request<{ pending: Pending }>("POST", `/api/pending/${id}/snooze`),
  notQuantifiable: (id: number) =>
    request<{ pending: Pending }>("POST", `/api/pending/${id}/not-quantifiable`),
  dismissPending: (id: number) => request<{ pending: Pending }>("POST", `/api/pending/${id}/dismiss`),

  // -------------------------------------------------------------------------
  // Ask and Remind me
  ask: (question: string) => request<AskResult>("POST", "/api/ask", { question }),
  remindMe: () => request<RemindMe>("GET", "/api/remind-me"),

  // -------------------------------------------------------------------------
  // Settings, push, export, data
  settings: () => request<Settings>("GET", "/api/settings"),
  putSettings: (patch: SettingsPatch) => request<Settings>("PUT", "/api/settings", patch),
  pushSubscribe: (sub: PushSubscriptionBody) => request<{ ok: true }>("POST", "/api/push/subscribe", sub),
  pushUnsubscribe: (endpoint: string) =>
    request<{ ok: true }>("DELETE", "/api/push/subscribe", { endpoint }),
  pushTest: () => request<{ sent: number }>("POST", "/api/push/test"),
  exportBrain: async () => (await rawRequest("GET", exportUrls.brain, undefined, {}, "text/markdown")).text(),
  deleteAllData: () => request<{ ok: true }>("POST", "/api/data/delete", { confirm: "delete" }),

  // -------------------------------------------------------------------------
  // Admin (owner only)
  adminOverview: (days = 30) => request<AdminOverview>("GET", `/api/admin/overview${qs({ days })}`),
  adminSessions: () => request<{ sessions: AdminSessionRow[] }>("GET", "/api/admin/sessions"),
  adminTrace: (id: string) => request<AdminTrace>("GET", `/api/admin/sessions/${enc(id)}/trace`),
  adminEvals: () => request<{ runs: EvalRun[] }>("GET", "/api/admin/evals"),
};

/** Export download links (plain GETs; the browser sends the cookie). */
export const exportUrls = {
  brain: "/api/export/brain",
  resumeAsCode: (since?: string) => `/api/export/resume-as-code${qs({ since })}`,
  markdown: "/api/export/markdown",
  sqlite: "/api/export/sqlite",
};

/**
 * End a session from `pagehide`. `sendBeacon` can't set headers, which is why the backend exempts
 * this one route from the X-Requested-With check. Body is text/plain JSON (allowed by the contract).
 */
export function endSessionBeacon(id: string, reason: "user" | "idle" = "user"): boolean {
  try {
    const blob = new Blob([JSON.stringify({ reason })], { type: "text/plain" });
    return navigator.sendBeacon(`/api/sessions/${enc(id)}/end`, blob);
  } catch {
    return false;
  }
}
