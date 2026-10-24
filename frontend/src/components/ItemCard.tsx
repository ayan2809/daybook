import { Link } from "react-router";
import type { MemoryItem } from "../types";
import { formatDay, kindLabel } from "../lib/format";

/** Compact item: title, bullet, metric displays. Used on Summary and Timeline. */
export function ItemCard({ item, link = true }: { item: MemoryItem; link?: boolean }) {
  const body = (
    <>
      <div className="item-meta">
        <span className={`kind kind-${item.kind}`}>{kindLabel(item.kind)}</span>
        <span>{formatDay(item.occurred_on)}</span>
        {item.project && <span>· {item.project.name}</span>}
        {item.status === "hidden" && <span className="tag">Hidden</span>}
      </div>
      <h3 className="item-title">{item.title}</h3>
      {item.bullet && <p className="item-bullet">{item.bullet}</p>}
      {item.metrics.length > 0 && (
        <ul className="metric-list">
          {item.metrics.map((m) => (
            <li key={m.id} className="metric">
              {m.display}
            </li>
          ))}
        </ul>
      )}
    </>
  );
  return link ? (
    <Link to={`/timeline/${item.id}`} className="card item-card link-card">
      {body}
    </Link>
  ) : (
    <article className="card item-card">{body}</article>
  );
}
