import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, errorMessage } from "../api";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { formatLongDay, kindLabel } from "../lib/format";
import type { RemindMe } from "../types";

export function Remind() {
  const [data, setData] = useState<RemindMe | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.remindMe().then(setData, (err) => setError(errorMessage(err)));
  }, []);

  return (
    <Page>
      <header className="page-head">
        <h1>What you've gotten through</h1>
        <p className="muted small">A few things from your own record.</p>
      </header>
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Loading />}
      {data && data.cards.length === 0 && <Notice tone="neutral">{data.empty_message ?? "Nothing here yet."}</Notice>}
      <div className="stack">
        {data?.cards.map((c) => (
          <Link key={c.item_id} to={`/timeline/${c.item_id}`} className="card link-card remind-card">
            <p className="muted small">
              {kindLabel(c.kind)} · {formatLongDay(c.occurred_on)}
            </p>
            <h2 className="item-title">{c.title}</h2>
            <p>{c.text}</p>
          </Link>
        ))}
      </div>
      <p className="muted small">
        Daybook isn't therapy. If you're struggling, Tele-MANAS is free and open 24×7 in India:{" "}
        <a href="tel:14416">14416</a>.
      </p>
    </Page>
  );
}
