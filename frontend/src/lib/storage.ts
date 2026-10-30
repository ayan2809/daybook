// localStorage can throw (private mode, blocked site data, previews). Every access goes through here.

export const KEYS = {
  debugNow: "daybook.debugNow",
  sessionId: "daybook.sessionId",
  draft: (sessionId: string) => `daybook.draft.${sessionId}`,
  outbox: (sessionId: string) => `daybook.outbox.${sessionId}`,
  recoveryKey: "daybook.recoveryKey", // this device's copy, so it unlocks itself (DB-32)
} as const;

export function getItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function setItem(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the app still works, it just can't restore */
  }
}

export function removeItem(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function getJSON<T>(key: string): T | null {
  const raw = getItem(key);
  if (raw == null) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function setJSON(key: string, value: unknown): void {
  setItem(key, JSON.stringify(value));
}
