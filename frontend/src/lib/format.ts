import type { BoardChip, Kind } from "../types";

const KIND_LABELS: Record<Kind, string> = {
  achievement: "Achievement",
  progress: "Progress",
  learning: "Learning",
  challenge: "Challenge",
  soft_skill: "Soft skill",
  decision: "Decision",
  blocker: "Blocker",
  praise: "Praise",
  plan: "Plan",
};
export const kindLabel = (k: Kind) => KIND_LABELS[k] ?? k;

export const CHIP_LABELS: Record<BoardChip, string> = {
  number_captured: "Number captured",
  needs_number: "Needs a number",
  soft_skill: "Soft skill",
  needs_outcome: "Needs an outcome",
  captured: "Captured",
};

/** Parse "YYYY-MM-DD" as a local calendar date (no timezone shift). */
export function parseDay(d: string): Date {
  const [y, m, day] = d.slice(0, 10).split("-").map(Number);
  return new Date(y, (m || 1) - 1, day || 1);
}

export function formatDay(d: string, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }): string {
  try {
    return parseDay(d).toLocaleDateString(undefined, opts);
  } catch {
    return d;
  }
}

export function formatLongDay(d: string): string {
  return formatDay(d, { weekday: "long", day: "numeric", month: "long" });
}

export function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

/** ISO week key ("2026-W43") and the Monday that starts it. */
export function isoWeek(d: string): { key: string; monday: Date } {
  const date = parseDay(d);
  const dow = (date.getDay() + 6) % 7; // Mon=0
  const monday = new Date(date);
  monday.setDate(date.getDate() - dow);
  const thursday = new Date(monday);
  thursday.setDate(monday.getDate() + 3);
  const yearStart = new Date(thursday.getFullYear(), 0, 1);
  const week = 1 + Math.floor((thursday.getTime() - yearStart.getTime()) / 86400000 / 7);
  return { key: `${thursday.getFullYear()}-W${String(week).padStart(2, "0")}`, monday };
}

export function formatClock(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function greeting(now = new Date()): string {
  const h = now.getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v == null ? "–" : `${(v * 100).toFixed(digits)}%`;
}

export function usd(v: number | null | undefined): string {
  return v == null ? "–" : `$${v.toFixed(v < 1 ? 4 : 2)}`;
}
