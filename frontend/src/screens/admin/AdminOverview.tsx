import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Flag } from "lucide-react";
import { api, errorMessage } from "../../api";
import { Page } from "../../components/Page";
import { Loading, Notice } from "../../components/Status";
import { formatClock, formatDateTime, pct, usd } from "../../lib/format";
import type { AdminOverview, AdminSessionRow, EvalRun } from "../../types";

export function AdminOverviewScreen() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [sessions, setSessions] = useState<AdminSessionRow[] | null>(null);
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOverview(null);
    api.adminOverview(days).then(setOverview, (err) => setError(errorMessage(err)));
  }, [days]);

  useEffect(() => {
    api.adminSessions().then((r) => setSessions(r.sessions), (err) => setError(errorMessage(err)));
    api.adminEvals().then((r) => setRuns(r.runs), () => setRuns([]));
  }, []);

  return (
    <Page wide>
      <header className="page-head row between">
        <h1>Admin</h1>
        <div className="segmented" role="group" aria-label="Period">
          {[7, 30].map((d) => (
            <button key={d} className={days === d ? "seg active" : "seg"} aria-pressed={days === d} onClick={() => setDays(d)}>
              {d} days
            </button>
          ))}
        </div>
      </header>
      {error && <Notice tone="error">{error}</Notice>}

      {!overview ? (
        !error && <Loading />
      ) : (
        <>
          <div className="tiles">
            <Tile label="Sessions" value={String(overview.sessions)} sub={`${overview.completed} completed`} />
            <Tile label="Completion rate" value={pct(overview.completion_rate)} />
            <Tile label="Median duration" value={formatClock(overview.median_duration_sec)} />
            <Tile label="Time-limit hits" value={String(overview.time_limit_hits)} />
            <Tile label="Cost total" value={usd(overview.cost_total_usd)} sub={`${usd(overview.cost_per_session_usd)} / session`} />
            <Tile
              label="Spend today"
              value={usd(overview.spend_today.total)}
              sub={`of ${usd(overview.spend_today.budget)} · demo ${usd(overview.spend_today.demo)}`}
            />
            <Tile label="Turn latency" value={`${overview.latency.turn_p50_ms} ms`} sub={`p95 ${overview.latency.turn_p95_ms} ms`} />
            <Tile label="TTS characters" value={overview.tts_chars.toLocaleString()} />
            <Tile label="Goal adherence" value={pct(overview.goal_adherence_avg)} />
            <Tile label="Fidelity issues" value={String(overview.fidelity_issues)} />
            <Tile label="Metric capture" value={pct(overview.metric_capture_rate)} />
            <Tile label="Pending resolved" value={pct(overview.pending_resolution_rate)} />
            <Tile label="Ask abstain rate" value={pct(overview.ask_abstain_rate)} />
            <Tile label="Feedback" value={`${overview.feedback.up} up · ${overview.feedback.down} down`} />
          </div>

          <div className="two-col">
            <section className="card">
              <h2 className="card-title">Latency by span</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Span</th>
                    <th scope="col">p50</th>
                    <th scope="col">p95</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(overview.latency.by_span).map(([name, v]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{v.p50_ms} ms</td>
                      <td>{v.p95_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section className="card">
              <h2 className="card-title">Guard failure rate</h2>
              <table className="table">
                <tbody>
                  {Object.entries(overview.guard_fail_rate).map(([name, v]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{pct(v, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
        </>
      )}

      <section className="card">
        <h2 className="card-title">Sessions</h2>
        {!sessions ? (
          <Loading />
        ) : sessions.length === 0 ? (
          <p className="muted small">No sessions yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Length</th>
                  <th scope="col">Turns</th>
                  <th scope="col">Status</th>
                  <th scope="col">Cost</th>
                  <th scope="col">p50</th>
                  <th scope="col">Guards</th>
                  <th scope="col">
                    <span className="visually-hidden">Flagged</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className={s.flagged ? "flagged" : ""}>
                    <td>
                      <Link to={`/admin/sessions/${encodeURIComponent(s.id)}`}>{s.local_date}</Link>
                    </td>
                    <td>{s.mode}</td>
                    <td>{formatClock(s.duration_sec)}</td>
                    <td>{s.turn_count}</td>
                    <td>
                      {s.status}
                      {s.end_reason ? ` (${s.end_reason})` : ""}
                    </td>
                    <td>{usd(s.cost_usd)}</td>
                    <td>{s.latency_p50_ms} ms</td>
                    <td>{s.guard_fails}</td>
                    <td>{s.flagged && <Flag size={16} aria-label="Flagged" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {runs && runs.length > 0 && (
        <section className="card">
          <h2 className="card-title">Eval runs</h2>
          <ul className="plain-list">
            {runs.map((r) => (
              <li key={r.id}>
                <strong>{formatDateTime(r.created_at)}</strong> · {r.mode}
                <div className="chips">
                  {Object.entries(r.suites).map(([name, s]) => (
                    <span key={name} className={s.passed ? "chip chip-number_captured" : "chip chip-needs_number"}>
                      {name} {s.score.toFixed(2)} / {s.gate}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Page>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  );
}
