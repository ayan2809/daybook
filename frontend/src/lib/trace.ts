// Pull the interesting attributes out of a turn trace (plan §13.1). Attribute names are the
// documented `daybook.*` ones; anything missing simply isn't shown.
import type { Trace, TraceSpan } from "../types";

export interface TraceHighlights {
  intent: string | null;
  move: string | null;
  target: string | null;
  gap: string | null;
  phase: string | null;
  retrievedIds: unknown[];
  guards: Record<string, boolean> | null;
  latencyMs: number | null;
  costUsd: number | null;
}

function findAttr(spans: TraceSpan[], key: string): unknown {
  for (const s of spans) {
    if (s.attrs && key in s.attrs) return s.attrs[key];
  }
  return undefined;
}

const str = (v: unknown) => (v == null ? null : String(v));

export function traceHighlights(t: Trace | null | undefined): TraceHighlights {
  const spans = t?.spans ?? [];
  const ids = findAttr(spans, "daybook.retrieval.ids");
  const guard = findAttr(spans, "daybook.guard");
  return {
    intent: str(findAttr(spans, "daybook.intent")),
    move: str(findAttr(spans, "daybook.move")),
    target: str(findAttr(spans, "daybook.target")),
    gap: str(findAttr(spans, "daybook.gap")),
    phase: str(findAttr(spans, "daybook.phase")),
    retrievedIds: Array.isArray(ids) ? ids : [],
    guards: guard && typeof guard === "object" ? (guard as Record<string, boolean>) : null,
    latencyMs: t?.totals?.latency_ms ?? null,
    costUsd: t?.totals?.cost_usd ?? null,
  };
}
