import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Mic, NotebookPen, Play, Zap } from "lucide-react";
import { api, errorMessage } from "../api";
import { unlockAudio } from "../audio";
import { Page } from "../components/Page";
import { PendingCard } from "../components/PendingCard";
import { Loading, Notice } from "../components/Status";
import { formatDay, formatLongDay, greeting, parseDay } from "../lib/format";
import type { Mode, Today } from "../types";

export function TodayScreen() {
  const navigate = useNavigate();
  const [today, setToday] = useState<Today | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setToday(await api.today());
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The tap that starts a reflection also unlocks audio for iOS (plan §9.3).
  function start(mode: Mode) {
    unlockAudio();
    navigate(`/reflect?mode=${mode}`);
  }
  function resume(id: string) {
    unlockAudio();
    navigate(`/reflect?session=${encodeURIComponent(id)}`);
  }

  if (!today) {
    return (
      <Page>
        {error ? (
          <Notice tone="error">
            {error}{" "}
            <button className="link" onClick={() => void load()}>
              Try again
            </button>
          </Notice>
        ) : (
          <Loading />
        )}
      </Page>
    );
  }

  const name = today.display_name ? `, ${today.display_name}` : "";
  return (
    <Page>
      <header className="today-head">
        <p className="muted">{formatLongDay(today.date)}</p>
        <h1>
          {greeting()}
          {name}
        </h1>
        {today.reflected_today && <p className="muted small">You've reflected today. Nice.</p>}
      </header>

      <div className="stack">
        {today.active_session_id ? (
          <button className="btn btn-primary btn-xl" onClick={() => resume(today.active_session_id!)}>
            <Play size={20} aria-hidden="true" /> Resume reflection
          </button>
        ) : (
          <button className="btn btn-primary btn-xl" onClick={() => start("guided")}>
            <Mic size={20} aria-hidden="true" /> Reflect on today
          </button>
        )}
        <div className="row gap">
          <button
            className="btn grow"
            onClick={() => start("dump")}
            disabled={!!today.active_session_id}
            aria-label="Brain dump: say everything in one go"
          >
            <NotebookPen size={18} aria-hidden="true" /> Brain dump
          </button>
          <button
            className="btn grow"
            onClick={() => start("quick")}
            disabled={!!today.active_session_id}
            aria-label="Quick log: one sentence, no follow-ups"
          >
            <Zap size={18} aria-hidden="true" /> Quick log
          </button>
        </div>
      </div>

      <PendingCard
        pending={today.pending}
        onResolved={(id) => setToday((t) => (t ? { ...t, pending: t.pending.filter((p) => p.id !== id) } : t))}
      />

      <section className="card" aria-labelledby="streak-title">
        <h2 id="streak-title" className="card-title">
          Last 14 days
        </h2>
        <ol className="streak" aria-label="Reflections in the last 14 days">
          {today.streak.map((d) => (
            <li
              key={d.date}
              className={d.reflected ? "dot on" : "dot"}
              title={`${formatDay(d.date)}${d.reflected ? ": reflected" : ""}`}
            >
              <span className="visually-hidden">
                {formatDay(d.date)} {d.reflected ? "reflected" : "no reflection"}
              </span>
              <span aria-hidden="true" className="dot-label">
                {parseDay(d.date).toLocaleDateString(undefined, { weekday: "narrow" })}
              </span>
            </li>
          ))}
        </ol>
      </section>

      {today.last_summary && (
        <section className="card" aria-labelledby="last-title">
          <h2 id="last-title" className="card-title">
            Last time · {formatDay(today.last_summary.date)}
          </h2>
          <p className="clamp-3">{today.last_summary.text}</p>
          <Link to={`/summary/${today.last_summary.session_id}`} className="small">
            See the notes
          </Link>
        </section>
      )}

      <p className="center">
        <Link to="/remind" className="gentle-link">
          Rough day? See what you've gotten through.
        </Link>
      </p>
    </Page>
  );
}
