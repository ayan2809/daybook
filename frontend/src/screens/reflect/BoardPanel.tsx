// "Captured so far": the draft board. Side panel on desktop (≥ 900 px), bottom sheet on mobile.
import { useState } from "react";
import { ChevronUp } from "lucide-react";
import { CHIP_LABELS, kindLabel } from "../../lib/format";
import type { Board } from "../../types";

export function BoardPanel({ board }: { board: Board }) {
  const [open, setOpen] = useState(false);
  const n = board.items.length;
  return (
    <aside className={open ? "board open" : "board"} aria-labelledby="board-title">
      <button
        className="board-handle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="board-body"
      >
        <span id="board-title">Captured so far{n > 0 ? ` (${n})` : ""}</span>
        <ChevronUp size={18} aria-hidden="true" className="board-chevron" />
      </button>
      <div id="board-body" className="board-body">
        {n === 0 ? (
          <p className="muted small">Things you mention will show up here.</p>
        ) : (
          <ul className="board-list">
            {board.items.map((it) => (
              <li key={it.temp_id} className="board-item">
                <div className="board-item-head">
                  <span className="board-item-title">{it.title}</span>
                  <span className={`chip chip-${it.chip}`}>{CHIP_LABELS[it.chip] ?? it.chip}</span>
                </div>
                <span className="muted small">{kindLabel(it.kind)}</span>
                {it.metrics_text.length > 0 && (
                  <ul className="metric-list">
                    {it.metrics_text.map((m) => (
                      <li key={m} className="metric">
                        {m}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
