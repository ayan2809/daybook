import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, errorMessage } from "../api";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { SummaryBody } from "../components/SummaryBody";
import { formatLongDay } from "../lib/format";
import type { Summary } from "../types";

const POLL_MS = 1500;

export function SummaryScreen() {
  const { id = "" } = useParams();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let failures = 0;
    async function poll() {
      try {
        const s = await api.summary(id);
        if (stopped) return;
        setSummary(s);
        setError(null);
        failures = 0;
        if (s.status === "finalized" || s.status === "failed") return;
      } catch (err) {
        if (stopped) return;
        failures++;
        setError(errorMessage(err));
        if (failures > 5) return;
      }
      timer = window.setTimeout(poll, POLL_MS);
    }
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  const done = summary?.status === "finalized";
  return (
    <Page>
      <header className="page-head">
        <h1>Today's notes</h1>
        {summary && <p className="muted">{formatLongDay(summary.date)}</p>}
      </header>
      {error && !summary && <Notice tone="error">{error}</Notice>}
      {summary?.status === "failed" && (
        <Notice tone="error">
          Something went wrong while writing up your notes. Your words are saved; they'll be processed again.
        </Notice>
      )}
      {!summary || (!done && summary.status !== "failed") ? (
        <Loading label="Writing up your notes…" />
      ) : null}
      {summary && (done || summary.items.length > 0 || summary.day_summary) && <SummaryBody summary={summary} />}
      <p>
        <Link to="/today">Back to Today</Link>
      </p>
    </Page>
  );
}
