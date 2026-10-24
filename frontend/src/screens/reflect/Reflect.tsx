// /reflect?mode=guided|dump|quick|edit&item_id=  (or ?session=<id> to resume)
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ArrowLeft, History, Volume2 } from "lucide-react";
import { api, ApiError, endSessionBeacon, errorMessage, isNetworkError } from "../../api";
import { isUnlocked, onSpeakingChange, playBlocked, playTurn, stopAudio, unlockAudio, type PlayOutcome } from "../../audio";
import { useMe } from "../../me";
import { Page } from "../../components/Page";
import { Ring } from "../../components/Ring";
import { SafetyCard } from "../../components/SafetyCard";
import { Loading, Notice } from "../../components/Status";
import { formatClock } from "../../lib/format";
import { uuid, useOnline } from "../../lib/platform";
import { getItem, getJSON, KEYS, removeItem, setItem, setJSON } from "../../lib/storage";
import type { Board, Mode, PendingAction, Phase, SafetyInfo, SessionView, TurnResult } from "../../types";
import { BoardPanel } from "./BoardPanel";
import { Composer, type Prefill } from "./Composer";
import { HowItDecided } from "./HowItDecided";
import { Replay } from "./Replay";
import { Transcript, type LocalTurn } from "./Transcript";

const MODES: Mode[] = ["guided", "dump", "quick", "edit"];
const MODE_LABELS: Record<Mode, string> = {
  guided: "Reflection",
  dump: "Brain dump",
  quick: "Quick log",
  edit: "Fix a memory",
};
const IDLE_MS = 60_000;

interface OutboxItem {
  client_turn_id: string;
  text: string;
  dictated_chars: number;
}

export function Reflect() {
  const { me } = useMe();
  const [params] = useSearchParams();
  const [replay, setReplay] = useState(false);
  if (me?.demo?.replay_only || replay) return <Replay />;

  const m = params.get("mode") as Mode | null;
  const mode: Mode = m && MODES.includes(m) ? m : "guided";
  const rawItem = params.get("item_id");
  const itemId = rawItem && /^\d+$/.test(rawItem) ? Number(rawItem) : undefined;
  return (
    <LiveReflect
      key={params.toString()}
      mode={mode}
      itemId={itemId}
      sessionParam={params.get("session")}
      onReplay={() => setReplay(true)}
    />
  );
}

function wasReload(): boolean {
  try {
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    return nav?.type === "reload";
  } catch {
    return false;
  }
}

function LiveReflect({
  mode,
  itemId,
  sessionParam,
  onReplay,
}: {
  mode: Mode;
  itemId?: number;
  sessionParam: string | null;
  onReplay: () => void;
}) {
  const navigate = useNavigate();
  const { me } = useMe();
  const online = useOnline();

  const [session, setSession] = useState<SessionView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [turns, setTurns] = useState<LocalTurn[]>([]);
  const [board, setBoard] = useState<Board>({ items: [] });
  const [, setPhase] = useState<Phase>("open");
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [safety, setSafety] = useState<SafetyInfo | null>(null);
  const [deadline, setDeadline] = useState(0);
  const [limitSec, setLimitSec] = useState(420);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [stalled, setStalled] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const [sendError, setSendError] = useState<string | null>(null);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [audioState, setAudioState] = useState<Record<string, "blocked" | "unavailable">>({});
  const [focusSignal, setFocusSignal] = useState(0);
  const [needsStart, setNeedsStart] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [stillThere, setStillThere] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [ending, setEnding] = useState(false);
  const [traceKey, setTraceKey] = useState(0);

  const sidRef = useRef<string | null>(null);
  const activeRef = useRef(false);
  const mountedRef = useRef(false);
  const initRef = useRef(false);
  const deliveringRef = useRef(false);
  const speakingRef = useRef(false);
  const lastActivityRef = useRef(Date.now());

  const touch = useCallback(() => {
    lastActivityRef.current = Date.now();
    setStillThere(false);
  }, []);

  // ---- leaving -------------------------------------------------------------

  const clearLocal = useCallback((sid: string) => {
    if (getItem(KEYS.sessionId) === sid) removeItem(KEYS.sessionId);
    removeItem(KEYS.draft(sid));
    removeItem(KEYS.outbox(sid));
  }, []);

  const finish = useCallback(
    (to: "summary" | "today" = "summary") => {
      const sid = sidRef.current;
      activeRef.current = false;
      if (!sid || !mountedRef.current) return;
      clearLocal(sid);
      stopAudio();
      navigate(to === "summary" ? `/summary/${encodeURIComponent(sid)}` : "/today", { replace: true });
    },
    [clearLocal, navigate],
  );

  // ---- audio ---------------------------------------------------------------

  const playReply = useCallback(
    async (turnId: string, fromTap = false): Promise<PlayOutcome> => {
      const outcome = await (fromTap ? playBlocked(turnId) : playTurn(turnId));
      if (outcome === "blocked" || outcome === "unavailable") {
        setAudioState((s) => ({ ...s, [turnId]: outcome }));
      } else if (outcome === "played") {
        setAudioState((s) => {
          if (!(turnId in s)) return s;
          const next = { ...s };
          delete next[turnId];
          return next;
        });
      }
      if (outcome !== "cancelled") {
        lastActivityRef.current = Date.now();
        setFocusSignal((n) => n + 1);
      }
      return outcome;
    },
    [],
  );

  useEffect(() => {
    const off = onSpeakingChange((v) => {
      speakingRef.current = v;
      setSpeaking(v);
    });
    return () => {
      off();
      stopAudio(); // stop on navigation away
    };
  }, []);

  // ---- delivering turns ----------------------------------------------------

  const onResult = useCallback(
    (item: OutboxItem, r: TurnResult) => {
      setTurns((ts) => {
        const seq = ts.reduce((m, t) => Math.max(m, t.seq), 0) + 1;
        return [
          ...ts.map((t) => (t.client_turn_id === item.client_turn_id ? { ...t, sending: false } : t)),
          {
            seq,
            role: "assistant",
            text: r.reply_text,
            created_at: new Date().toISOString(),
            turn_id: r.turn_id,
            safety: !!r.safety,
          },
        ];
      });
      setPhase(r.phase);
      setDeadline(Date.now() + r.seconds_left * 1000);
      setBoard(r.board);
      setPendingAction(r.pending_action);
      setSuggestions(r.suggestions ?? []);
      setSafety(r.safety);
      setTraceKey((k) => k + 1);
      lastActivityRef.current = Date.now();
      if (r.ended) {
        activeRef.current = false;
        setFinishing(true);
      }
      const playing: Promise<PlayOutcome> = r.voice ? playReply(r.turn_id) : Promise.resolve("none");
      if (!r.voice) setFocusSignal((n) => n + 1);
      if (r.ended) void playing.then(() => finish("summary"));
    },
    [finish, playReply],
  );

  const deliver = useCallback(
    async (item: OutboxItem) => {
      const sid = sidRef.current;
      if (!sid || deliveringRef.current) return;
      deliveringRef.current = true;
      setBusy(true);
      try {
        const r = await api.sendTurn(sid, item);
        removeItem(KEYS.outbox(sid));
        setStalled(false);
        if (mountedRef.current) onResult(item, r);
      } catch (err) {
        if (isNetworkError(err)) {
          // Keep the outbox; retry with the same client_turn_id when we're back online.
          setStalled(true);
          setRetryNonce((n) => n + 1);
          return;
        }
        removeItem(KEYS.outbox(sid));
        setStalled(false);
        if (err instanceof ApiError && (err.code === "time_up" || err.code === "session_ended")) {
          finish("summary");
          return;
        }
        if (err instanceof ApiError && err.code === "demo_cap") {
          onReplay();
          return;
        }
        // Put the text back so nothing is lost.
        setTurns((ts) => ts.filter((t) => t.client_turn_id !== item.client_turn_id));
        setPrefill({ text: item.text, nonce: Date.now() });
        setSendError(errorMessage(err));
      } finally {
        deliveringRef.current = false;
        setBusy(false);
      }
    },
    [finish, onReplay, onResult],
  );

  // Retry a stalled turn when the network is back.
  useEffect(() => {
    if (!stalled || !online) return;
    const t = window.setTimeout(() => {
      const sid = sidRef.current;
      const out = sid ? getJSON<OutboxItem>(KEYS.outbox(sid)) : null;
      if (out) void deliver(out);
      else setStalled(false);
    }, 2500);
    return () => clearTimeout(t);
  }, [stalled, online, retryNonce, deliver]);

  const send = useCallback(
    (text: string, dictated: number) => {
      const sid = sidRef.current;
      if (!sid || deliveringRef.current || !activeRef.current) return;
      setSendError(null);
      touch();
      stopAudio(); // the user is talking now
      const item: OutboxItem = { client_turn_id: uuid(), text, dictated_chars: dictated };
      setJSON(KEYS.outbox(sid), item);
      setTurns((ts) => [
        ...ts,
        {
          seq: ts.reduce((m, t) => Math.max(m, t.seq), 0) + 1,
          role: "user",
          text,
          created_at: new Date().toISOString(),
          turn_id: null,
          safety: false,
          sending: true,
          client_turn_id: item.client_turn_id,
        },
      ]);
      void deliver(item);
    },
    [deliver, touch],
  );

  // ---- start or restore ------------------------------------------------------

  const adopt = useCallback(
    (s: SessionView, fresh: boolean) => {
      sidRef.current = s.id;
      activeRef.current = s.status === "active";
      setItem(KEYS.sessionId, s.id);
      setSession(s);
      setTurns(s.turns);
      setBoard(s.board);
      setPhase(s.phase);
      setPendingAction(s.pending_action);
      setSuggestions(s.suggestions ?? []);
      setLimitSec(s.limit_sec);
      setDeadline(Date.now() + s.seconds_left * 1000);
      lastActivityRef.current = Date.now();

      // A turn that was typed but never acknowledged (offline, reload): resend it.
      const out = getJSON<OutboxItem>(KEYS.outbox(s.id));
      if (out) {
        const lastUser = [...s.turns].reverse().find((t) => t.role === "user");
        if (lastUser && lastUser.text === out.text) {
          removeItem(KEYS.outbox(s.id));
        } else {
          setTurns((ts) => [
            ...ts,
            {
              seq: ts.reduce((m, t) => Math.max(m, t.seq), 0) + 1,
              role: "user",
              text: out.text,
              created_at: new Date().toISOString(),
              turn_id: null,
              safety: false,
              sending: true,
              client_turn_id: out.client_turn_id,
            },
          ]);
          void deliver(out);
          return;
        }
      }

      const opener = [...s.turns].reverse().find((t) => t.role === "assistant");
      if (fresh && s.voice_enabled && opener?.turn_id) {
        if (isUnlocked()) void playReply(opener.turn_id);
        else setNeedsStart(true); // e.g. opened from a notification: wait for a tap (iOS)
      } else {
        setFocusSignal((n) => n + 1);
      }
    },
    [deliver, playReply],
  );

  useEffect(() => {
    mountedRef.current = true;
    if (!initRef.current) {
      initRef.current = true;
      void (async () => {
        const stored = sessionParam || getItem(KEYS.sessionId);
        if (stored) {
          try {
            const { session: s } = await api.getSession(stored);
            if (s.status === "active") return adopt(s, false);
            // It ended while we were away (e.g. the reload's pagehide ended it): show its notes.
            if (sessionParam || wasReload()) {
              clearLocal(s.id);
              navigate(`/summary/${encodeURIComponent(s.id)}`, { replace: true });
              return;
            }
            clearLocal(s.id);
          } catch (err) {
            if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
              clearLocal(stored);
            } else {
              setLoadError(errorMessage(err));
              return;
            }
          }
        }
        try {
          const { session: s } = await api.startSession(mode, itemId);
          adopt(s, true);
        } catch (err) {
          if (err instanceof ApiError && err.code === "session_active" && typeof err.extra.session_id === "string") {
            try {
              const { session: s } = await api.getSession(err.extra.session_id);
              adopt(s, false);
            } catch (e2) {
              setLoadError(errorMessage(e2));
            }
          } else if (err instanceof ApiError && err.code === "demo_cap") {
            onReplay();
          } else {
            setLoadError(errorMessage(err));
          }
        }
      })();
    }
    return () => {
      mountedRef.current = false;
    };
    // Runs once per mount; the component is keyed by its URL params.
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // End the session if the page is closed mid-reflection.
  useEffect(() => {
    const onHide = (e: PageTransitionEvent) => {
      if (e.persisted) return; // kept in the back/forward cache: it may come back
      if (activeRef.current && sidRef.current) endSessionBeacon(sidRef.current, "user");
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  // Clock tick (countdown) + idle check.
  useEffect(() => {
    const i = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (
        activeRef.current &&
        !speakingRef.current &&
        !deliveringRef.current &&
        t - lastActivityRef.current > IDLE_MS
      ) {
        setStillThere(true);
      }
    }, 1000);
    return () => clearInterval(i);
  }, []);

  // ---- actions --------------------------------------------------------------

  async function endSession(to: "summary" | "today") {
    const sid = sidRef.current;
    if (!sid) return;
    setEnding(true);
    try {
      await api.endSession(sid, "user");
    } catch (err) {
      const alreadyOver = err instanceof ApiError && (err.status === 409 || err.status === 404);
      if (!alreadyOver) {
        setSendError(errorMessage(err));
        setEnding(false);
        return;
      }
    }
    finish(to);
  }

  function startVoice() {
    unlockAudio();
    setNeedsStart(false);
    const opener = [...turns].reverse().find((t) => t.role === "assistant");
    if (opener?.turn_id) void playReply(opener.turn_id, true);
  }

  // ---- render ---------------------------------------------------------------

  if (loadError) {
    return (
      <Page tabs={false}>
        <Notice tone="error">{loadError}</Notice>
        <Link to="/today">Back to Today</Link>
      </Page>
    );
  }
  if (!session) {
    return (
      <Page tabs={false}>
        <Loading label="Starting your reflection…" />
      </Page>
    );
  }

  const left = Math.max(0, (deadline - now) / 1000);
  const latest = [...turns].reverse().find((t) => t.role === "assistant");
  const latestAudio = latest?.turn_id ? audioState[latest.turn_id] : undefined;
  const voiceOn = session.voice_enabled && (me?.features.voice ?? true);
  const autoSend = me?.settings?.auto_send ?? true;
  const waiting = busy && !stalled;

  return (
    <Page tabs={false} className="reflect">
      <div className="reflect-grid">
        <div className="reflect-main">
          <header className="reflect-head">
            <Link to="/today" className="icon-btn" aria-label="Back to Today (the reflection stays open)">
              <ArrowLeft size={20} aria-hidden="true" />
            </Link>
            <Ring
              progress={limitSec ? left / limitSec : 0}
              size={60}
              label={`${formatClock(left)} left`}
              className={left <= 30 ? "ring-low" : ""}
            >
              <span className="ring-clock">{formatClock(left)}</span>
            </Ring>
            <span className="reflect-mode">{MODE_LABELS[session.mode]}</span>
            <div className="row gap-sm">
              <button
                className={showTranscript ? "icon-btn selected" : "icon-btn"}
                onClick={() => setShowTranscript((v) => !v)}
                aria-pressed={showTranscript}
                aria-label="Show transcript"
              >
                <History size={20} aria-hidden="true" />
              </button>
              <button className="btn btn-sm" onClick={() => void endSession("summary")} disabled={ending || finishing}>
                End
              </button>
            </div>
          </header>

          {(!online || stalled) && <Notice tone="neutral">You're offline. Your draft is saved.</Notice>}

          {safety && <SafetyCard info={safety} onPause={() => void endSession("today")} pausing={ending} />}

          <div className="agent-line-wrap">
            <p className={speaking ? "agent-line speaking" : "agent-line"} aria-live="polite">
              {latest?.text ?? ""}
            </p>
            {waiting && (
              <p className="thinking" aria-label="Daybook is thinking">
                <span />
                <span />
                <span />
              </p>
            )}
            {voiceOn && latest?.turn_id && !needsStart && (
              <div className="row gap-sm audio-row">
                {latestAudio === "blocked" ? (
                  <button className="btn btn-sm" onClick={() => void playReply(latest.turn_id!, true)}>
                    <Volume2 size={16} aria-hidden="true" /> Tap to hear
                  </button>
                ) : latestAudio === "unavailable" ? (
                  <span className="muted small">Voice unavailable</span>
                ) : (
                  !speaking && (
                    <button
                      className="icon-btn"
                      onClick={() => {
                        unlockAudio();
                        void playReply(latest.turn_id!, true);
                      }}
                      aria-label="Hear this again"
                    >
                      <Volume2 size={18} aria-hidden="true" />
                    </button>
                  )
                )}
              </div>
            )}
            {pendingAction && <p className="pending-action">{pendingAction.prompt}</p>}
          </div>

          {needsStart && voiceOn && (
            <button className="btn btn-primary btn-xl" onClick={startVoice}>
              Start reflection
            </button>
          )}

          {left === 0 && activeRef.current && !finishing && (
            <p className="muted small">Time's up. Send a last thought, or tap End.</p>
          )}
          {finishing && <p className="muted small">Wrapping up…</p>}

          {stillThere && !finishing && (
            <div className="card still-there" role="status">
              <p>Still there? Take your time.</p>
              <div className="row gap-sm">
                <button className="btn btn-sm" onClick={touch}>
                  I'm here
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => void endSession("summary")}>
                  Wrap up for now
                </button>
              </div>
            </div>
          )}

          {sendError && <Notice tone="error">{sendError}</Notice>}

          {suggestions.length > 0 && !finishing && (
            <div className="chips" aria-label="Suggested replies">
              {suggestions.map((s) => (
                <button key={s} className="chip chip-suggest" onClick={() => send(s, 0)} disabled={busy || stalled}>
                  {s}
                </button>
              ))}
            </div>
          )}

          {!finishing && (
            <Composer
              sessionId={session.id}
              autoSend={autoSend}
              canSend={!busy && !stalled}
              onSend={send}
              focusSignal={focusSignal}
              prefill={prefill}
              onActivity={touch}
            />
          )}

          {showTranscript && <Transcript turns={turns} />}
          {me?.role === "demo" && <HowItDecided refreshKey={traceKey} />}
        </div>
        <BoardPanel board={board} />
      </div>
    </Page>
  );
}
