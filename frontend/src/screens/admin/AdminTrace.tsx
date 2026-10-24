import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { api, errorMessage } from "../../api";
import { Page } from "../../components/Page";
import { Loading, Notice } from "../../components/Status";
import { formatClock, formatDateTime, usd } from "../../lib/format";
import { traceHighlights } from "../../lib/trace";
import type { AdminTrace, Trace } from "../../types";

export function AdminTraceScreen() {
  const { id = "" } = useParams();
  const [data, setData] = useState<AdminTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.adminTrace(id).then(setData, (err) => setError(errorMessage(err)));
  }, [id]);

  return (
    <Page wide>
      <Link to="/admin" className="back-link">
        <ArrowLeft size={16} aria-hidden="true" /> Admin
      </Link>
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? (
        !error && <Loading />
      ) : (
        <>
          <header className="page-head">
            <h1>
              Session {data.session.local_date} · {data.session.mode}
            </h1>
            <p className="muted small">
              {formatDateTime(data.session.started_at)} · {formatClock(data.session.duration_sec)} ·{" "}
              {data.session.turn_count} turns · {usd(data.session.cost_usd)} · {data.session.status}
              {data.session.flagged ? " · flagged" : ""}
            </p>
          </header>
          {data.session.judge && (
            <details className="card">
              <summary>Session judge</summary>
              <pre className="json">{JSON.stringify(data.session.judge, null, 2)}</pre>
            </details>
          )}
          <section className="stack-sm">
            <h2 className="section-title">Turns</h2>
            {data.turns.map((t, i) => (
              <TraceCard key={`t${t.seq ?? i}`} t={t} />
            ))}
          </section>
          {data.jobs.length > 0 && (
            <section className="stack-sm">
              <h2 className="section-title">Jobs</h2>
              {data.jobs.map((t, i) => (
                <TraceCard key={`j${i}`} t={t} />
              ))}
            </section>
          )}
        </>
      )}
    </Page>
  );
}

function TraceCard({ t }: { t: Trace }) {
  const h = traceHighlights(t);
  const guardFails = h.guards ? Object.entries(h.guards).filter(([, ok]) => !ok).map(([k]) => k) : [];
  return (
    <article className="card trace">
      <div className="trace-head">
        <strong>{t.seq != null ? `#${t.seq}` : (t.name ?? "job")}</strong>
        <span className="muted small">
          {h.latencyMs != null ? `${h.latencyMs} ms` : ""} {h.costUsd != null ? `· ${usd(h.costUsd)}` : ""}
        </span>
      </div>
      {t.user_text && (
        <p className="small">
          <span className="muted">User:</span> {t.user_text}
        </p>
      )}
      {t.reply_text && (
        <p className="small">
          <span className="muted">Reply:</span> {t.reply_text}
        </p>
      )}
      <dl className="kv small">
        <dt>Intent</dt>
        <dd>{h.intent ?? "–"}</dd>
        <dt>Move</dt>
        <dd>
          {h.move ?? "–"}
          {h.target ? ` → ${h.target}` : ""}
          {h.gap ? ` (${h.gap})` : ""}
          {h.phase ? ` · ${h.phase}` : ""}
        </dd>
        <dt>Retrieved</dt>
        <dd>{h.retrievedIds.length ? h.retrievedIds.join(", ") : "none"}</dd>
        <dt>Guards</dt>
        <dd className={guardFails.length ? "error" : ""}>
          {h.guards ? (guardFails.length ? `failed: ${guardFails.join(", ")}` : "all passed") : "–"}
        </dd>
      </dl>
      <details>
        <summary className="small">Spans ({t.spans.length})</summary>
        <ul className="span-list">
          {t.spans.map((s, i) => (
            <li key={s.span_id ?? i}>
              <details>
                <summary className="small">
                  {s.name} <span className="muted">{s.kind ?? ""}</span>{" "}
                  {s.latency_ms != null && <span className="muted">{s.latency_ms} ms</span>}{" "}
                  {s.status && s.status !== "ok" && <span className="error">{s.status}</span>}
                </summary>
                <pre className="json">{JSON.stringify(s, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}
