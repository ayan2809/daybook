// Demo only: "How it decided" shows this sandbox's last turn trace (plan §15, P1).
import { useState } from "react";
import { api, errorMessage } from "../../api";
import { traceHighlights } from "../../lib/trace";
import type { Trace } from "../../types";

export function HowItDecided({ refreshKey }: { refreshKey: number }) {
  const [trace, setTrace] = useState<Trace | null | undefined>(undefined);
  const [loadedFor, setLoadedFor] = useState(-1);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (loadedFor === refreshKey) return;
    try {
      setTrace(await api.demoLastTrace());
      setLoadedFor(refreshKey);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const h = traceHighlights(trace);
  return (
    <details className="drawer" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && void load()}>
      <summary>How it decided</summary>
      {error && <p className="error small">{error}</p>}
      {trace === null && <p className="muted small">No turn yet.</p>}
      {trace && (
        <dl className="kv small">
          <dt>Intent</dt>
          <dd>{h.intent ?? "–"}</dd>
          <dt>Move</dt>
          <dd>
            {h.move ?? "–"}
            {h.gap ? ` (${h.gap})` : ""}
          </dd>
          <dt>Retrieved</dt>
          <dd>{h.retrievedIds.length ? h.retrievedIds.join(", ") : "none"}</dd>
          <dt>Guards</dt>
          <dd>
            {h.guards
              ? Object.entries(h.guards)
                  .map(([k, v]) => `${k} ${v ? "✓" : "✗"}`)
                  .join(" · ")
              : "–"}
          </dd>
          <dt>Latency</dt>
          <dd>{h.latencyMs != null ? `${h.latencyMs} ms` : "–"}</dd>
        </dl>
      )}
    </details>
  );
}
