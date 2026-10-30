// "2 things to confirm": open pending questions, each answerable by typing.
// answer → readback → Confirm / Snooze / No number / Dismiss. Nothing is saved until Confirm.
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorMessage } from "../api";
import type { Pending, PendingCandidate } from "../types";

export function PendingCard({ pending, onResolved }: { pending: Pending[]; onResolved: (id: number) => void }) {
  if (pending.length === 0) return null;
  const n = pending.length;
  return (
    <section className="card" aria-labelledby="pending-title">
      <h2 id="pending-title" className="card-title">
        {n === 1 ? "1 thing to confirm" : `${n} things to confirm`}
      </h2>
      <ul className="pending-list">
        {pending.map((p) => (
          <li key={p.id}>
            <PendingRow p={p} onResolved={() => onResolved(p.id)} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function PendingRow({ p, onResolved }: { p: Pending; onResolved: () => void }) {
  const [text, setText] = useState("");
  const [candidate, setCandidate] = useState<PendingCandidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, resolves = true) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (resolves) onResolved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    void run(async () => {
      const r = await api.answerPending(p.id, t);
      setCandidate(r.candidate);
    }, false);
  }

  return (
    <div className="pending">
      <p className="pending-q">{p.question}</p>
      {p.item_title &&
        (p.item_id != null ? (
          <Link className="muted small" to={`/timeline/${p.item_id}`}>
            {p.item_title}
          </Link>
        ) : (
          <span className="muted small">{p.item_title}</span>
        ))}
      {!candidate ? (
        <form className="row gap" onSubmit={submit}>
          <label className="visually-hidden" htmlFor={`pa-${p.id}`}>
            Your answer
          </label>
          <input
            id={`pa-${p.id}`}
            className="input grow"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type or dictate your answer"
            disabled={busy}
          />
          <button className="btn" type="submit" disabled={busy || !text.trim()}>
            Check
          </button>
        </form>
      ) : (
        <div className="readback">
          <p>{candidate.readback}</p>
          <div className="row wrap gap-sm">
            <button
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={() => void run(() => api.confirmPending(p.id, candidate))}
            >
              Confirm
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => setCandidate(null)}>
              Change answer
            </button>
          </div>
        </div>
      )}
      <div className="row wrap gap-sm pending-actions">
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void run(() => api.snoozePending(p.id))}>
          Snooze
        </button>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void run(() => api.notQuantifiable(p.id))}>
          No number
        </button>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void run(() => api.dismissPending(p.id))}>
          Dismiss
        </button>
      </div>
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
