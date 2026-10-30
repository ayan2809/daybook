import { useState, type KeyboardEvent } from "react";
import { Link } from "react-router";
import { Send } from "lucide-react";
import { api, ApiError, errorMessage } from "../api";
import { useMe } from "../me";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { formatDay } from "../lib/format";
import type { AskResult, ItemRef } from "../types";

const DEMO_SUGGESTIONS = [
  "What did I ship in the last two weeks?",
  "Give me my strongest resume bullet",
  "When did I present to the CFO?",
  "Remind me of a hard week I got through",
];
const OWNER_SUGGESTIONS = [
  "What did I ship this month?",
  "Give me my strongest resume bullet",
  "What did I learn recently?",
];

interface Exchange {
  question: string;
  result?: AskResult;
  error?: string;
}

export function Ask() {
  const { me } = useMe();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Exchange[]>([]);
  const suggestions = me?.role === "demo" ? DEMO_SUGGESTIONS : OWNER_SUGGESTIONS;

  async function ask(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setText("");
    setHistory((h) => [{ question }, ...h]);
    let entry: Exchange;
    try {
      entry = { question, result: await api.ask(question) };
    } catch (err) {
      let msg = errorMessage(err);
      if (err instanceof ApiError && err.code === "budget") msg = "Daily budget reached. Try again tomorrow.";
      if (err instanceof ApiError && err.code === "demo_cap") msg = "The demo has used its questions for today.";
      entry = { question, error: msg };
    }
    setHistory((h) => [entry, ...h.slice(1)]);
    setBusy(false);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask(text);
    }
  }

  return (
    <Page>
      <header className="page-head">
        <h1>Ask your record</h1>
        <p className="muted small">Answers come only from your notes, with sources.</p>
      </header>

      <div className="composer">
        <label htmlFor="ask-q" className="visually-hidden">
          Your question
        </label>
        <textarea
          id="ask-q"
          className="composer-input"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask anything about your work"
          enterKeyHint="send"
        />
        <div className="composer-bar">
          <span className="muted small">Enter to ask</span>
          <button
            className="icon-btn icon-btn-primary"
            onClick={() => void ask(text)}
            disabled={busy || !text.trim()}
            aria-label="Ask"
          >
            <Send size={18} aria-hidden="true" />
          </button>
        </div>
      </div>

      {history.length === 0 && (
        <div className="chips" aria-label="Suggested questions">
          {suggestions.map((s) => (
            <button key={s} className="chip chip-suggest" onClick={() => void ask(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      )}

      {history.map((x, i) => (
        <section key={`${history.length - i}`} className="answer-block" aria-label={x.question}>
          <p className="question">{x.question}</p>
          {!x.result && !x.error && <Loading label="Looking through your notes…" />}
          {x.error && <Notice tone="error">{x.error}</Notice>}
          {x.result && <Answer r={x.result} />}
        </section>
      ))}
    </Page>
  );
}

function RefChip({ r }: { r: ItemRef }) {
  return (
    <Link to={`/timeline/${r.id}`} className="chip chip-cite" title={r.title}>
      {formatDay(r.occurred_on)} · {r.title}
    </Link>
  );
}

function Answer({ r }: { r: AskResult }) {
  if (r.abstained) {
    return (
      <div className="answer abstained">
        <p>{r.answer || "I couldn't find that in your notes."}</p>
        {r.closest.length > 0 && (
          <>
            <p className="muted small">Closest notes:</p>
            <div className="chips">
              {r.closest.map((c) => (
                <RefChip key={c.id} r={c} />
              ))}
            </div>
          </>
        )}
      </div>
    );
  }
  const sentences = r.sentences.length > 0 ? r.sentences : [{ text: r.answer, item_ids: [] as number[] }];
  return (
    <div className="answer">
      {sentences.map((s, i) => (
        <p key={i} className="answer-sentence">
          {s.text}{" "}
          {s.item_ids.map((id) => {
            const ref = r.refs[String(id)];
            return ref ? <RefChip key={id} r={ref} /> : null;
          })}
        </p>
      ))}
    </div>
  );
}
