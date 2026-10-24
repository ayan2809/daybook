import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api, errorMessage } from "../api";
import { ItemCard } from "../components/ItemCard";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { formatDay, isoWeek, kindLabel } from "../lib/format";
import { KINDS, type Kind, type MemoryList } from "../types";

export function Timeline() {
  const [data, setData] = useState<MemoryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [project, setProject] = useState<number | null>(null);
  const [kind, setKind] = useState<Kind | null>(null);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);

  // Debounce the search box.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let stale = false;
    api
      .memories({ project: project ?? undefined, kind: kind ?? undefined, q: query || undefined, include_hidden: showHidden })
      .then(
        (d) => {
          if (stale) return;
          setData(d);
          setError(null);
          // Keep the full project list from the unfiltered view so chips don't disappear.
          if (d.projects.length >= projects.length) setProjects(d.projects);
        },
        (err) => !stale && setError(errorMessage(err)),
      );
    return () => {
      stale = true;
    };
  }, [project, kind, query, showHidden]);

  const weeks = useMemo(() => {
    const groups: { key: string; monday: Date; items: MemoryList["items"] }[] = [];
    for (const it of data?.items ?? []) {
      const w = isoWeek(it.occurred_on);
      let g = groups.find((x) => x.key === w.key);
      if (!g) {
        g = { key: w.key, monday: w.monday, items: [] };
        groups.push(g);
      }
      g.items.push(it);
    }
    return groups;
  }, [data]);

  return (
    <Page>
      <header className="page-head">
        <h1>Timeline</h1>
      </header>

      <div className="search">
        <Search size={18} aria-hidden="true" />
        <label htmlFor="tl-q" className="visually-hidden">
          Search your record
        </label>
        <input
          id="tl-q"
          type="search"
          className="input"
          placeholder="Search your record"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {projects.length > 0 && (
        <div className="chips" role="group" aria-label="Filter by project">
          <FilterChip active={project === null} onClick={() => setProject(null)} label="All projects" />
          {projects.map((p) => (
            <FilterChip key={p.id} active={project === p.id} onClick={() => setProject(project === p.id ? null : p.id)} label={p.name} />
          ))}
        </div>
      )}
      <div className="chips" role="group" aria-label="Filter by kind">
        <FilterChip active={kind === null} onClick={() => setKind(null)} label="All kinds" />
        {KINDS.map((k) => (
          <FilterChip key={k} active={kind === k} onClick={() => setKind(kind === k ? null : k)} label={kindLabel(k)} />
        ))}
        <FilterChip active={showHidden} onClick={() => setShowHidden((v) => !v)} label="Show hidden" />
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Loading />}
      {data && data.items.length === 0 && <Notice tone="neutral">Nothing here yet.</Notice>}

      {weeks.map((w) => (
        <section key={w.key} className="stack-sm week" aria-label={`Week of ${w.monday.toDateString()}`}>
          <h2 className="section-title">
            Week of {formatDay(`${w.monday.getFullYear()}-${String(w.monday.getMonth() + 1).padStart(2, "0")}-${String(w.monday.getDate()).padStart(2, "0")}`)}
          </h2>
          {w.items.map((it) => (
            <ItemCard key={it.id} item={it} />
          ))}
        </section>
      ))}
    </Page>
  );
}

function FilterChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button className={active ? "chip chip-filter active" : "chip chip-filter"} aria-pressed={active} onClick={onClick}>
      {label}
    </button>
  );
}
