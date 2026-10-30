import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, errorMessage } from "../api";
import { ArrowDown, BellRing, Check, Lock, Mic } from "lucide-react";
import { useMe } from "../me";
import { GoogleButton } from "../components/GoogleButton";

export function Landing() {
  const navigate = useNavigate();
  const { me, refresh } = useMe();
  const [busy, setBusy] = useState<"demo" | "google" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const clientId = me?.features.google_client_id ?? null;
  const inDemo = me?.role === "demo";

  async function run(kind: "demo" | "google", action: () => Promise<unknown>) {
    setBusy(kind);
    setError(null);
    try {
      await action();
      await refresh();
      navigate("/today");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="shell landing-shell">
      <main className="landing">
        <div className="landing-copy">
          <div className="brand">
            <img src="/pwa-192x192.png" alt="" width={32} height={32} />
            <span>Daybook</span>
          </div>
          <h1 className="landing-title">Your workday, remembered.</h1>
          <p className="landing-sub">
            Talk for five minutes each evening. Daybook asks the follow-ups and turns your answers into a
            resume-ready record.
          </p>

          <ul className="facts">
            <li>
              <Mic size={18} aria-hidden="true" /> Five minutes, by voice or typing
            </li>
            <li>
              <BellRing size={18} aria-hidden="true" /> An evening reminder on your phone
            </li>
            <li>
              <Lock size={18} aria-hidden="true" /> Encrypted with a key only you hold
            </li>
          </ul>

          <div className="landing-actions">
            {clientId ? (
              <GoogleButton
                clientId={clientId}
                onCredential={(credential) => void run("google", () => api.loginGoogle(credential))}
              />
            ) : (
              <p className="small muted">Google sign-in isn't set up on this server yet.</p>
            )}
            {inDemo ? (
              <button className="btn btn-xl" onClick={() => navigate("/today")} disabled={busy !== null}>
                Continue the demo
              </button>
            ) : (
              <button className="btn btn-xl" onClick={() => void run("demo", api.demoStart)} disabled={busy !== null}>
                {busy === "demo" ? "Setting up the demo…" : "Try the demo, no sign-in"}
              </button>
            )}
            {busy === "google" && <p className="small">Signing you in…</p>}
            {error && <p className="error small">{error}</p>}
            <p className="small muted landing-note">
              New? Google sign-in creates your account.{" "}
              {!showLogin && (
                <button className="link small" onClick={() => setShowLogin(true)}>
                  Owner key
                </button>
              )}
            </p>
            {showLogin && (
              <OwnerLogin
                onDone={async () => {
                  await refresh();
                  navigate("/today");
                }}
              />
            )}
          </div>
        </div>

        <Glimpse />
      </main>
    </div>
  );
}

/** What a reflection looks like, in three steps (lines from the made-up demo persona). */
function Glimpse() {
  return (
    <figure className="glimpse" aria-label="Example: a question, your answer, and the saved record">
      <p className="g-bubble g-agent">
        <span className="g-who">Daybook</span>
        What changed after the typo fix?
      </p>
      <p className="g-bubble g-user">
        <span className="g-who">You</span>
        Empty searches went from about 12% to 7%.
      </p>
      <div className="g-arrow" aria-hidden="true">
        <ArrowDown size={16} />
      </div>
      <div className="g-record">
        <span className="g-tag">
          <Check size={14} aria-hidden="true" /> Saved for your resume
        </span>
        <p>Cut empty help-centre searches from 12% to 7% by adding typo tolerance.</p>
      </div>
    </figure>
  );
}

export function OwnerLogin({ onDone }: { onDone: () => void | Promise<void> }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(key.trim());
      setKey("");
      await onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack-sm" onSubmit={submit}>
      <label htmlFor="owner-key" className="small">
        Owner key
      </label>
      <div className="row gap">
        <input
          id="owner-key"
          className="input grow"
          type="password"
          autoComplete="current-password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button className="btn" type="submit" disabled={busy || !key.trim()}>
          Sign in
        </button>
      </div>
      {error && <p className="error small">{error}</p>}
    </form>
  );
}
