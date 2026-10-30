// The reply box. Dictation-friendly: one input event that adds ≥ 20 chars counts as a dictation
// burst; if the text then stays unchanged for 1.5 s it sends itself (with a visible ring). Any
// keystroke-sized change cancels that. Typed text sends only on Enter (Shift+Enter = newline).
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Send, X } from "lucide-react";
import { Ring } from "../../components/Ring";
import { getItem, KEYS, removeItem, setItem } from "../../lib/storage";
import { isIOS } from "../../lib/platform";

const BURST_CHARS = 20;
const AUTO_SEND_MS = 1500;

export interface Prefill {
  text: string;
  nonce: number;
}

export function Composer({
  sessionId,
  autoSend,
  canSend,
  onSend,
  focusSignal,
  prefill,
  onActivity,
}: {
  sessionId: string;
  autoSend: boolean;
  /** False while a turn is in flight; sending waits until it's true again. */
  canSend: boolean;
  onSend: (text: string, dictatedChars: number) => void;
  /** Bump to focus the box (after the agent finishes speaking). */
  focusSignal: number;
  prefill: Prefill | null;
  onActivity: () => void;
}) {
  const draftKey = KEYS.draft(sessionId);
  const [text, setText] = useState(() => getItem(draftKey) ?? "");
  const [armedAt, setArmedAt] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const dictatedRef = useRef(0);
  const ref = useRef<HTMLTextAreaElement>(null);
  const textRef = useRef(text);
  textRef.current = text;
  const canSendRef = useRef(canSend);
  canSendRef.current = canSend;

  useEffect(() => {
    if (focusSignal > 0) ref.current?.focus({ preventScroll: true });
  }, [focusSignal]);

  useEffect(() => {
    if (!prefill) return;
    setText(prefill.text);
    setItem(draftKey, prefill.text);
    setArmedAt(null);
  }, [prefill, draftKey]);

  function update(v: string) {
    setText(v);
    if (v) setItem(draftKey, v);
    else removeItem(draftKey);
  }

  function submit(): boolean {
    const t = textRef.current.trim();
    if (!t || !canSendRef.current) return false;
    const dictated = Math.min(dictatedRef.current, t.length);
    dictatedRef.current = 0;
    setArmedAt(null);
    update("");
    onSend(t, dictated);
    return true;
  }

  // Auto-send countdown.
  useEffect(() => {
    if (armedAt == null) {
      setProgress(0);
      return;
    }
    let raf = 0;
    let retry: number | undefined;
    const tick = () => {
      const p = (Date.now() - armedAt) / AUTO_SEND_MS;
      setProgress(Math.min(1, p));
      if (p >= 1) {
        if (!submit()) {
          // A turn is still in flight: try again shortly, keep the ring full.
          retry = window.setTimeout(tick, 400);
        }
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (retry) clearTimeout(retry);
    };
  }, [armedAt]);

  function onChange(e: ChangeEvent<HTMLTextAreaElement>) {
    const v = e.target.value;
    const added = v.length - textRef.current.length;
    if (added >= BURST_CHARS) {
      dictatedRef.current += added;
      setArmedAt(autoSend ? Date.now() : null);
    } else if (v !== textRef.current) {
      setArmedAt(null); // keystroke-sized change cancels auto-send
    }
    if (!v.trim()) dictatedRef.current = 0;
    update(v);
    onActivity();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const hint = isIOS() ? "Tap Start Flow on your keyboard, or type" : "Hold Fn to talk with Wispr Flow, or type";

  return (
    <div className="composer">
      <label htmlFor="reply" className="visually-hidden">
        Your reply
      </label>
      <textarea
        id="reply"
        ref={ref}
        className="composer-input"
        value={text}
        onChange={onChange}
        onKeyDown={onKeyDown}
        rows={3}
        placeholder={hint}
        aria-describedby="reply-hint"
        enterKeyHint="send"
      />
      <div className="composer-bar">
        <span id="reply-hint" className="muted small">
          {armedAt != null ? "Sending… keep typing to cancel" : hint}
        </span>
        <div className="row gap-sm">
          {armedAt != null && (
            <>
              <Ring progress={progress} size={32} stroke={3} label="Auto-send countdown" className="autosend-ring" />
              <button className="icon-btn" onClick={() => setArmedAt(null)} aria-label="Cancel auto-send">
                <X size={18} aria-hidden="true" />
              </button>
            </>
          )}
          <button
            className="icon-btn icon-btn-primary"
            onClick={() => submit()}
            disabled={!text.trim() || !canSend}
            aria-label="Send reply"
          >
            <Send size={18} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
