import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, errorMessage } from "../api";
import { useMe } from "../me";
import { GoogleButton } from "../components/GoogleButton";

export function Landing() {
  const navigate = useNavigate();
  const { me, refresh } = useMe();
  const [busy, setBusy] = useState<"demo" | "google" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const clientId = me?.features.google_client_id ?? null;

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
    <div className="shell">
      <main className="page landing">
        <header className="landing-head">
          <img src="/pwa-192x192.png" alt="" width={56} height={56} className="logo" />
          <h1>Daybook</h1>
          <p className="pitch">
            A 5-minute evening voice conversation about your workday that turns into a resume-ready record.
          </p>
        </header>

        <section className="card signin-card" aria-labelledby="signin-title">
          <h2 id="signin-title" className="card-title">
            Sign in or create your Daybook
          </h2>
          {clientId ? (
            <GoogleButton
              clientId={clientId}
              onCredential={(credential) => void run("google", () => api.loginGoogle(credential))}
            />
          ) : (
            <p className="small muted">Google sign-in isn't set up on this server yet.</p>
          )}
          <p className="small muted">
            New here? Signing in with Google creates your account. Your reflections are private to it: nobody
            else can see them, and you can export or delete everything in Settings.
          </p>
          {busy === "google" && <p className="small">Signing you in…</p>}
        </section>

        <div className="or" aria-hidden="true">
          <span>or</span>
        </div>

        <button className="btn btn-xl" onClick={() => void run("demo", api.demoStart)} disabled={busy !== null}>
          {busy === "demo" ? "Setting up the demo…" : "Try the demo (no sign-in)"}
        </button>
        {error && <p className="error">{error}</p>}

        <section aria-labelledby="how-title" className="how">
          <h2 id="how-title" className="section-title">
            How it works
          </h2>
          <ol className="steps">
            <li>
              <strong>Talk for five minutes.</strong> In the evening, Daybook asks about your day, one question at a
              time. Answer with Wispr Flow, your keyboard's dictation, or by typing.
            </li>
            <li>
              <strong>It asks for the numbers.</strong> When something's vague, it asks what changed and by how much.
              Don't know yet? It asks again tomorrow.
            </li>
            <li>
              <strong>Get a record you can use.</strong> Clean, resume-ready bullets with sources, and you can ask
              your record anything later.
            </li>
          </ol>
        </section>

        <div className="owner-signin">
          {showLogin ? (
            <OwnerLogin
              onDone={async () => {
                await refresh();
                navigate("/today");
              }}
            />
          ) : (
            <button className="link small" onClick={() => setShowLogin(true)}>
              Sign in with an owner key
            </button>
          )}
        </div>
      </main>
    </div>
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
