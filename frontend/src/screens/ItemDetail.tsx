import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, Eye, EyeOff, Mic, Pencil, RotateCcw } from "lucide-react";
import { api, errorMessage } from "../api";
import { unlockAudio } from "../audio";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { formatDateTime, formatLongDay, kindLabel } from "../lib/format";
import { KINDS, type MemoryDetail, type MemoryItem, type MemoryPatchFields, type Ownership } from "../types";

const OWNERSHIP: Ownership[] = ["led", "did", "helped", "team"];

export function ItemDetail() {
  const { id: rawId = "" } = useParams();
  const id = Number(rawId);
  const navigate = useNavigate();
  const [data, setData] = useState<MemoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.memory(id));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <Page>
        {error ? <Notice tone="error">{error}</Notice> : <Loading />}
      </Page>
    );
  }

  const { item, versions } = data;
  const hidden = item.status === "hidden";
  return (
    <Page>
      <Link to="/timeline" className="back-link">
        <ArrowLeft size={16} aria-hidden="true" /> Timeline
      </Link>
      <header className="page-head">
        <p className="muted small">
          {kindLabel(item.kind)} · {formatLongDay(item.occurred_on)}
          {item.project ? ` · ${item.project.name}` : ""}
          {hidden ? " · Hidden" : ""}
        </p>
        <h1>{item.title}</h1>
      </header>

      {error && <Notice tone="error">{error}</Notice>}

      {editing ? (
        <EditForm
          item={item}
          busy={busy}
          onCancel={() => setEditing(false)}
          onSave={(fields, reason) =>
            void act(async () => {
              await api.patchMemory(item.id, fields, reason);
              setEditing(false);
            })
          }
        />
      ) : (
        <>
          {item.bullet && <p className="bullet-quote">{item.bullet}</p>}
          <dl className="fields">
            <Field label="What" value={item.what} />
            <Field label="How" value={item.how} />
            <Field label="Result" value={item.result} />
            <Field label="Scope" value={item.scope} />
            <Field label="Your part" value={item.ownership} />
            {item.skills.length > 0 && <Field label="Skills" value={item.skills.join(", ")} />}
          </dl>

          {item.metrics.length > 0 && (
            <section className="card">
              <h2 className="card-title">Numbers</h2>
              <ul className="plain-list">
                {item.metrics.map((m) => (
                  <li key={m.id}>
                    <span className="metric">{m.display}</span>
                    {m.approx && <span className="muted small"> (approx.)</span>}
                    {m.source_text && <div className="muted small">“{m.source_text}”</div>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {item.validation_note && <Notice tone="neutral">{item.validation_note}</Notice>}

          {item.source_quote && (
            <blockquote className="source-quote">
              <span className="muted small">You said:</span> “{item.source_quote}”
            </blockquote>
          )}

          {item.open_pending.length > 0 && (
            <section className="card">
              <h2 className="card-title">Open questions</h2>
              <ul className="plain-list">
                {item.open_pending.map((p) => (
                  <li key={p.id}>{p.question}</li>
                ))}
              </ul>
            </section>
          )}

          <div className="row wrap gap">
            <button className="btn" onClick={() => setEditing(true)} disabled={busy}>
              <Pencil size={16} aria-hidden="true" /> Edit
            </button>
            <button
              className="btn"
              onClick={() => {
                unlockAudio();
                navigate(`/reflect?mode=edit&item_id=${item.id}`);
              }}
            >
              <Mic size={16} aria-hidden="true" /> Fix by voice
            </button>
            <button
              className="btn btn-ghost"
              disabled={busy}
              onClick={() => void act(() => (hidden ? api.unhideMemory(item.id) : api.hideMemory(item.id)))}
            >
              {hidden ? <Eye size={16} aria-hidden="true" /> : <EyeOff size={16} aria-hidden="true" />}
              {hidden ? "Unhide" : "Hide"}
            </button>
          </div>
        </>
      )}

      {versions.length > 0 && (
        <section aria-labelledby="versions-title" className="stack-sm">
          <h2 id="versions-title" className="section-title">
            Version history
          </h2>
          <ol className="versions">
            {versions.map((v) => (
              <li key={v.version} className="version">
                <div>
                  <strong>v{v.version}</strong> · {v.changed_by} · {formatDateTime(v.created_at)}
                  {v.reason && <div className="muted small">{v.reason}</div>}
                  {typeof v.snapshot.title === "string" && <div className="small">{v.snapshot.title}</div>}
                </div>
                {v.version !== item.version && (
                  <button
                    className="btn btn-sm"
                    disabled={busy}
                    onClick={() => void act(() => api.restoreMemory(item.id, v.version))}
                    aria-label={`Restore version ${v.version}`}
                  >
                    <RotateCcw size={14} aria-hidden="true" /> Restore
                  </button>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </Page>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

type FormState = Required<Omit<MemoryPatchFields, "ownership">> & { ownership: Ownership | "" };

function EditForm({
  item,
  busy,
  onSave,
  onCancel,
}: {
  item: MemoryItem;
  busy: boolean;
  onSave: (fields: MemoryPatchFields, reason?: string) => void;
  onCancel: () => void;
}) {
  const initial: FormState = {
    title: item.title,
    what: item.what,
    how: item.how ?? "",
    result: item.result ?? "",
    bullet: item.bullet ?? "",
    kind: item.kind,
    ownership: item.ownership ?? "",
    occurred_on: item.occurred_on,
  };
  const [f, setF] = useState<FormState>(initial);
  const [reason, setReason] = useState("");
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }));

  function submit(e: FormEvent) {
    e.preventDefault();
    const changed: MemoryPatchFields = {};
    if (f.title !== initial.title) changed.title = f.title;
    if (f.what !== initial.what) changed.what = f.what;
    if (f.how !== initial.how) changed.how = f.how;
    if (f.result !== initial.result) changed.result = f.result;
    if (f.bullet !== initial.bullet) changed.bullet = f.bullet;
    if (f.kind !== initial.kind) changed.kind = f.kind;
    if (f.ownership !== initial.ownership && f.ownership) changed.ownership = f.ownership;
    if (f.occurred_on !== initial.occurred_on) changed.occurred_on = f.occurred_on;
    if (Object.keys(changed).length === 0) return onCancel();
    onSave(changed, reason.trim() || undefined);
  }

  return (
    <form className="stack-sm edit-form" onSubmit={submit}>
      <TextField label="Title" value={f.title} onChange={(v) => set("title", v)} />
      <TextField label="What" value={f.what} onChange={(v) => set("what", v)} multiline />
      <TextField label="How" value={f.how} onChange={(v) => set("how", v)} multiline />
      <TextField label="Result" value={f.result} onChange={(v) => set("result", v)} multiline />
      <TextField label="Resume bullet" value={f.bullet} onChange={(v) => set("bullet", v)} multiline />
      <div className="row gap wrap">
        <label className="field">
          <span>Kind</span>
          <select className="input" value={f.kind} onChange={(e) => set("kind", e.target.value as FormState["kind"])}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {kindLabel(k)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Your part</span>
          <select
            className="input"
            value={f.ownership}
            onChange={(e) => set("ownership", e.target.value as FormState["ownership"])}
          >
            <option value="">–</option>
            {OWNERSHIP.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Date</span>
          <input
            className="input"
            type="date"
            value={f.occurred_on}
            onChange={(e) => set("occurred_on", e.target.value)}
          />
        </label>
      </div>
      <TextField label="Why the change (optional)" value={reason} onChange={setReason} />
      <div className="row gap">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Save
        </button>
        <button className="btn btn-ghost" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function TextField({
  label,
  value,
  onChange,
  multiline = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  multiline?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {multiline ? (
        <textarea className="input" rows={2} value={value} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input className="input" value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}
