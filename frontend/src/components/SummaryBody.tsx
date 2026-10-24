// The body of a session summary. Used by /summary/:id and the demo replay.
import { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errorMessage } from "../api";
import { ItemCard } from "./ItemCard";
import type { Summary } from "../types";

export function SummaryBody({ summary, interactive = true }: { summary: Summary; interactive?: boolean }) {
  return (
    <div className="stack">
      {summary.day_summary && (
        <section className="card">
          <h2 className="card-title">Your day</h2>
          <p>{summary.day_summary}</p>
        </section>
      )}

      {summary.items.length > 0 && (
        <section aria-labelledby="items-title" className="stack-sm">
          <h2 id="items-title" className="section-title">
            What you did
          </h2>
          {summary.items.map((it) => (
            <ItemCard key={it.id} item={it} link={interactive} />
          ))}
        </section>
      )}

      {summary.pending_created.length > 0 && (
        <section className="card" aria-labelledby="tomorrow-title">
          <h2 id="tomorrow-title" className="card-title">
            Tomorrow I'll ask about…
          </h2>
          <ul className="plain-list">
            {summary.pending_created.map((p) => (
              <li key={p.id}>{p.question}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="muted small">
        {Math.round(summary.stats.duration_sec / 60)} min · {summary.stats.turns} turns
        {summary.stats.spoken_pct != null &&
          ` · about ${Math.round(summary.stats.spoken_pct <= 1 ? summary.stats.spoken_pct * 100 : summary.stats.spoken_pct)}% of this reflection was spoken (an estimate)`}
      </p>

      {interactive && <Feedback sessionId={summary.session_id} initial={summary.feedback} />}
    </div>
  );
}

function Feedback({ sessionId, initial }: { sessionId: string; initial: "up" | "down" | null }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);

  async function send(v: "up" | "down") {
    const prev = value;
    setValue(v);
    setError(null);
    try {
      await api.feedback(sessionId, v);
    } catch (err) {
      setValue(prev);
      setError(errorMessage(err));
    }
  }

  return (
    <div className="feedback">
      <span>Was this helpful?</span>
      <button
        className={value === "up" ? "icon-btn selected" : "icon-btn"}
        aria-pressed={value === "up"}
        aria-label="Yes, helpful"
        onClick={() => void send("up")}
      >
        <ThumbsUp size={20} aria-hidden="true" />
      </button>
      <button
        className={value === "down" ? "icon-btn selected" : "icon-btn"}
        aria-pressed={value === "down"}
        aria-label="Not helpful"
        onClick={() => void send("down")}
      >
        <ThumbsDown size={20} aria-hidden="true" />
      </button>
      {error && <span className="error small">{error}</span>}
    </div>
  );
}
