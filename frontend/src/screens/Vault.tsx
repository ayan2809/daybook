import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Copy, Download, KeyRound, LogOut } from "lucide-react";
import { api, ApiError, errorMessage } from "../api";
import { useMe } from "../me";
import { deviceKey, downloadKey, newRecoveryKey } from "../lib/vault";
import { Loading } from "../components/Status";

/**
 * The sealed vault (DB-32). A new account gets a recovery key made here and shown once; a known
 * device unlocks itself with its saved copy; a new device asks for the key.
 */
export function VaultGate() {
  const { me } = useMe();
  if (me?.account?.vault === "new") return <SetupKey />;
  return <UnlockKey />;
}

function Shell({ children }: { children: React.ReactNode }) {
  const { refresh } = useMe();
  async function signOut() {
    deviceKey.forget();
    try {
      await api.logout();
    } catch {
      /* the cookie may already be gone */
    }
    await refresh();
  }
  return (
    <div className="shell landing-shell">
      <main className="vault">
        <div className="brand">
          <img src="/pwa-192x192.png" alt="" width={32} height={32} />
          <span>Daybook</span>
        </div>
        {children}
        <button className="link small vault-out" onClick={() => void signOut()}>
          <LogOut size={14} aria-hidden="true" /> Sign out
        </button>
      </main>
    </div>
  );
}

function SetupKey() {
  const { me, refresh } = useMe();
  const [key] = useState(newRecoveryKey);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const email = me?.account?.email ?? null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Couldn't copy. Select the key and copy it by hand.");
    }
  }

  async function open() {
    setBusy(true);
    setError(null);
    try {
      await api.vaultSetup(key);
      deviceKey.set(key);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h1 className="vault-title">Your recovery key</h1>
      <p className="vault-sub">
        Everything you tell Daybook is encrypted with this key before it's stored. Daybook doesn't keep a
        copy, so nobody else can read your reflections, including whoever runs the servers.
      </p>
      <KeyText value={key} />
      <div className="row gap-sm wrap">
        <button className="btn btn-ghost" onClick={() => void copy()}>
          {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
          {copied ? "Copied" : "Copy"}
        </button>
        <button className="btn btn-ghost" onClick={() => downloadKey(key, email)}>
          <Download size={16} aria-hidden="true" /> Download
        </button>
      </div>
      <p className="small muted">
        Save it in your password manager. This device remembers it; you'll need it on a new phone or
        laptop. If it's lost, your data can't be recovered by anyone.
      </p>
      <label className="vault-check">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>I've saved my recovery key</span>
      </label>
      <button className="btn btn-primary btn-xl" disabled={!saved || busy} onClick={() => void open()}>
        {busy ? "Sealing your vault…" : "Open my Daybook"}
      </button>
      {error && <p className="error small">{error}</p>}
    </Shell>
  );
}

function UnlockKey() {
  const { refresh } = useMe();
  const stored = useRef(deviceKey.get());
  const [auto, setAuto] = useState(Boolean(stored.current));
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lost, setLost] = useState(false);

  async function unlock(value: string, remember: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.vaultUnlock(value);
      if (remember) deviceKey.set(value.trim().toUpperCase());
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && err.code === "wrong_key" && !remember) deviceKey.forget();
      setError(errorMessage(err));
      setAuto(false);
      setBusy(false);
    }
  }

  useEffect(() => {
    if (stored.current) void unlock(stored.current, false);
    // run once, with the key this device saved
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (auto) return <Loading full />;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (key.trim()) void unlock(key, true);
  }

  async function startOver() {
    setBusy(true);
    try {
      await api.vaultReset();
      deviceKey.forget();
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h1 className="vault-title">
        <KeyRound size={26} aria-hidden="true" /> Unlock your Daybook
      </h1>
      <p className="vault-sub">
        Your reflections are encrypted with your recovery key. Paste it once and this device will remember it.
      </p>
      <form className="stack-sm" onSubmit={submit}>
        <input
          className="input vault-input"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          aria-label="Recovery key"
        />
        <button className="btn btn-primary btn-xl" disabled={busy || !key.trim()}>
          {busy ? "Unlocking…" : "Unlock"}
        </button>
      </form>
      {error && <p className="error small">{error}</p>}
      {!lost ? (
        <button className="link small" onClick={() => setLost(true)}>
          Lost your key?
        </button>
      ) : (
        <div className="vault-lost stack-sm">
          <p className="small">
            Without the key, nobody can open your old reflections, not even Daybook. You can start over with an
            empty Daybook and a new key. The old data stays sealed.
          </p>
          <button className="btn btn-ghost danger" disabled={busy} onClick={() => void startOver()}>
            Start over with a new key
          </button>
        </div>
      )}
    </Shell>
  );
}

/** The key in groups of four, wrapping only between groups. */
export function KeyText({ value }: { value: string }) {
  const groups = value.split("-");
  return (
    <div className="vault-key" aria-label="Recovery key">
      {groups.map((g, i) => (
        <span key={i}>
          {g}
          {i < groups.length - 1 && (
            <>
              -<wbr />
            </>
          )}
        </span>
      ))}
    </div>
  );
}
