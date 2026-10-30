// Demo replay mode (caps reached): plays back a recorded session with its real audio
// (GET /api/demo/replay), then shows its summary.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Play, Square } from "lucide-react";
import { api, errorMessage } from "../../api";
import { onSpeakingChange, playUrl, stopAudio, unlockAudio } from "../../audio";
import { Page } from "../../components/Page";
import { Loading, Notice } from "../../components/Status";
import { SummaryBody } from "../../components/SummaryBody";
import { Transcript } from "./Transcript";
import type { DemoReplay, ReplayTurn } from "../../types";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function Replay() {
  const [data, setData] = useState<DemoReplay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<ReplayTurn[]>([]);
  const [state, setState] = useState<"idle" | "playing" | "done">("idle");
  const [speaking, setSpeaking] = useState(false);
  const runRef = useRef(0);

  useEffect(() => {
    api.demoReplay().then(setData, (err) => setError(errorMessage(err)));
    const off = onSpeakingChange(setSpeaking);
    return () => {
      off();
      runRef.current++;
      stopAudio();
    };
  }, []);

  async function play() {
    if (!data) return;
    unlockAudio();
    const run = ++runRef.current;
    setShown([]);
    setState("playing");
    for (const t of data.turns) {
      if (runRef.current !== run) return;
      setShown((s) => [...s, t]);
      if (t.role === "assistant" && t.audio_url) {
        const outcome = await playUrl(t.audio_url);
        if (outcome !== "played") await wait(Math.min(6000, 1200 + t.text.length * 45));
      } else {
        await wait(Math.min(5000, 1000 + t.text.length * 30));
      }
    }
    if (runRef.current === run) setState("done");
  }

  function stop() {
    runRef.current++;
    stopAudio();
    setState("done");
  }

  if (error) {
    return (
      <Page tabs={false}>
        <Notice tone="error">{error}</Notice>
        <Link to="/today">Back to Today</Link>
      </Page>
    );
  }
  if (!data) {
    return (
      <Page tabs={false}>
        <Loading />
      </Page>
    );
  }

  const lastAgent = [...shown].reverse().find((t) => t.role === "assistant");
  return (
    <Page tabs={false} className="reflect-replay">
      <Notice tone="neutral">{data.banner}</Notice>
      <div className="agent-line-wrap">
        <p className={speaking ? "agent-line speaking" : "agent-line"} aria-live="polite">
          {lastAgent?.text ?? "A recorded reflection, with its real voice and notes."}
        </p>
      </div>
      <div className="row gap">
        {state !== "playing" ? (
          <button className="btn btn-primary" onClick={() => void play()}>
            <Play size={18} aria-hidden="true" /> {state === "idle" ? "Play the recording" : "Play again"}
          </button>
        ) : (
          <button className="btn" onClick={stop}>
            <Square size={16} aria-hidden="true" /> Stop
          </button>
        )}
        <Link to="/today" className="btn btn-ghost">
          Back to Today
        </Link>
      </div>
      {shown.length > 0 && <Transcript turns={shown} />}
      {state === "done" && (
        <section aria-labelledby="replay-summary" className="stack">
          <h2 id="replay-summary" className="section-title">
            The notes it wrote
          </h2>
          <SummaryBody summary={data.summary} interactive={false} />
        </section>
      )}
    </Page>
  );
}
