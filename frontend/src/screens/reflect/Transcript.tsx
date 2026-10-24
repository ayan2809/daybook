import type { Turn } from "../../types";

export interface LocalTurn extends Turn {
  /** A user turn that hasn't been acknowledged by the server yet. */
  sending?: boolean;
  /** Set on user turns typed in this tab, to match the server's acknowledgement. */
  client_turn_id?: string;
}

export function Transcript({ turns }: { turns: LocalTurn[] }) {
  return (
    <ol className="transcript" aria-label="Transcript">
      {turns.map((t, i) => (
        <li key={`${t.seq}-${i}`} className={`bubble bubble-${t.role}${t.sending ? " sending" : ""}`}>
          <span className="visually-hidden">{t.role === "assistant" ? "Daybook:" : "You:"}</span>
          {t.text}
          {t.sending && <span className="muted small"> · sending</span>}
        </li>
      ))}
    </ol>
  );
}
