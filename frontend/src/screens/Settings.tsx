import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { Bell, Download, LogOut } from "lucide-react";
import { api, errorMessage, exportUrls } from "../api";
import { useMe } from "../me";
import { Page } from "../components/Page";
import { Loading, Notice } from "../components/Status";
import { isIOS, isStandalone } from "../lib/platform";
import { OwnerLogin } from "./Landing";
import type { Settings, SettingsPatch } from "../types";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LENGTHS: Settings["session_limit_sec"][] = [300, 360, 420];

export function SettingsScreen() {
  const { me, refresh, setSettings: setMeSettings } = useMe();
  const navigate = useNavigate();
  const [settings, setSettings] = useState<Settings | null>(me?.settings ?? null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const isOwner = me?.role === "owner";

  useEffect(() => {
    api.settings().then(setSettings, (err) => setError(errorMessage(err)));
  }, []);

  async function save(patch: SettingsPatch) {
    setError(null);
    setSaved(false);
    try {
      const s = await api.putSettings(patch);
      setSettings(s);
      setMeSettings(s);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function signOut() {
    try {
      await api.logout();
    } catch {
      /* the cookie may already be gone */
    }
    await refresh();
    navigate("/", { replace: true });
  }

  return (
    <Page>
      <header className="page-head row between">
        <h1>Settings</h1>
        <span className="muted small" role="status" aria-live="polite">
          {saved ? "Saved" : ""}
        </span>
      </header>
      {error && <Notice tone="error">{error}</Notice>}
      {!settings ? (
        !error && <Loading />
      ) : (
        <>
          <Section title="Reminders">
            <Toggle
              label="Evening reminder"
              checked={settings.reminder.enabled}
              onChange={(v) => void save({ reminder: { enabled: v } })}
            />
            <label className="field-row">
              <span>Time</span>
              <input
                type="time"
                className="input input-auto"
                value={settings.reminder.time}
                onChange={(e) => e.target.value && void save({ reminder: { time: e.target.value } })}
              />
            </label>
            <div className="field-row">
              <span id="days-label">Days</span>
              <div className="chips" role="group" aria-labelledby="days-label">
                {DAYS.map((d, i) => {
                  const n = i + 1;
                  const on = settings.reminder.days.includes(n);
                  return (
                    <button
                      key={d}
                      className={on ? "chip chip-filter active" : "chip chip-filter"}
                      aria-pressed={on}
                      onClick={() => {
                        const days = on ? settings.reminder.days.filter((x) => x !== n) : [...settings.reminder.days, n].sort();
                        void save({ reminder: { days } });
                      }}
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>
            <Toggle
              label="Second nudge if I haven't reflected"
              checked={settings.reminder.second_nudge}
              onChange={(v) => void save({ reminder: { second_nudge: v } })}
            />
            {settings.reminder.second_nudge && (
              <label className="field-row">
                <span>Second nudge at</span>
                <input
                  type="time"
                  className="input input-auto"
                  value={settings.reminder.second_time}
                  onChange={(e) => e.target.value && void save({ reminder: { second_time: e.target.value } })}
                />
              </label>
            )}
          </Section>

          <Section title="Reflection">
            <div className="field-row">
              <span id="len-label">Session length</span>
              <div className="segmented" role="group" aria-labelledby="len-label">
                {LENGTHS.map((l) => (
                  <button
                    key={l}
                    className={settings.session_limit_sec === l ? "seg active" : "seg"}
                    aria-pressed={settings.session_limit_sec === l}
                    onClick={() => void save({ session_limit_sec: l })}
                  >
                    {l / 60} min
                  </button>
                ))}
              </div>
            </div>
            <Toggle label="Voice replies" checked={settings.voice} onChange={(v) => void save({ voice: v })} />
            <Toggle
              label="Auto-send dictated text"
              hint="After a dictation burst, sends when the text stays still for 1.5 s."
              checked={settings.auto_send}
              onChange={(v) => void save({ auto_send: v })}
            />
            <div className="field-row">
              <span id="pend-label">Ask yesterday's questions</span>
              <div className="segmented" role="group" aria-labelledby="pend-label">
                {(["start", "end"] as const).map((p) => (
                  <button
                    key={p}
                    className={settings.pending_at === p ? "seg active" : "seg"}
                    aria-pressed={settings.pending_at === p}
                    onClick={() => void save({ pending_at: p })}
                  >
                    At the {p}
                  </button>
                ))}
              </div>
            </div>
            <Toggle
              label="Keep people's names"
              hint="Off: people are noted by role (“my lead”), not by name."
              checked={settings.keep_names}
              onChange={(v) => void save({ keep_names: v })}
            />
          </Section>

          <Section title="Profile">
            <ProfileForm settings={settings} onSave={(profile) => void save({ profile })} />
          </Section>
        </>
      )}

      {isOwner && <PushSection />}

      {isOwner && (
        <Section title="Export">
          <div className="stack-sm">
            <a className="btn btn-ghost justify-start" href={exportUrls.resumeAsCode()} download>
              <Download size={16} aria-hidden="true" /> Resume-as-code (JSON)
            </a>
            <a className="btn btn-ghost justify-start" href={exportUrls.markdown} download>
              <Download size={16} aria-hidden="true" /> Markdown
            </a>
            <a className="btn btn-ghost justify-start" href={exportUrls.sqlite} download>
              <Download size={16} aria-hidden="true" /> SQLite file
            </a>
          </div>
        </Section>
      )}

      <Section title="Account">
        {isOwner ? (
          <div className="stack-sm">
            <Link to="/admin" className="btn btn-ghost justify-start">
              Admin
            </Link>
            <button className="btn btn-ghost justify-start" onClick={() => void signOut()}>
              <LogOut size={16} aria-hidden="true" /> Sign out
            </button>
          </div>
        ) : (
          <div className="stack-sm">
            <OwnerLogin
              onDone={async () => {
                await refresh();
                navigate("/today");
              }}
            />
            <button className="btn btn-ghost justify-start" onClick={() => void signOut()}>
              <LogOut size={16} aria-hidden="true" /> Leave the demo
            </button>
          </div>
        )}
      </Section>

      {isOwner && <DeleteSection onDeleted={() => void signOut()} />}

      <Section title="About">
        <p className="small">
          Daybook helps you keep a record of your work. It isn't therapy or a mental-health service. If you're
          struggling, please reach someone you trust. In India, Tele-MANAS is free and open 24×7:{" "}
          <a href="tel:14416">14416</a>.
        </p>
        <p className="small muted">
          Your reflection text goes to Google Gemini and the voice provider. Describe work at resume level; never
          dictate confidential details.
        </p>
      </Section>
    </Page>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `s-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className="card settings-section" aria-labelledby={id}>
      <h2 id={id} className="card-title">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="toggle-row">
      <span>
        {label}
        {hint && <span className="muted small block">{hint}</span>}
      </span>
      <input type="checkbox" role="switch" className="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function ProfileForm({ settings, onSave }: { settings: Settings; onSave: (p: SettingsPatch["profile"]) => void }) {
  const [name, setName] = useState(settings.profile.display_name ?? "");
  const [org, setOrg] = useState(settings.profile.org ?? "");
  const [title, setTitle] = useState(settings.profile.role_title ?? "");

  function submit(e: FormEvent) {
    e.preventDefault();
    onSave({ display_name: name.trim() || null, org: org.trim() || null, role_title: title.trim() || null });
  }

  return (
    <form className="stack-sm" onSubmit={submit}>
      <label className="field">
        <span>Display name</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="nickname" />
      </label>
      <label className="field">
        <span>Organisation (for export)</span>
        <input className="input" value={org} onChange={(e) => setOrg(e.target.value)} autoComplete="organization" />
      </label>
      <label className="field">
        <span>Role title (for export)</span>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoComplete="organization-title" />
      </label>
      <p className="muted small">Time zone: {settings.profile.timezone}</p>
      <button className="btn" type="submit">
        Save profile
      </button>
    </form>
  );
}

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function swReady(ms = 5000): Promise<ServiceWorkerRegistration> {
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("The service worker isn't active (it runs in the production build).")), ms),
    ),
  ]);
}

function PushSection() {
  const { me } = useMe();
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subscribed, setSubscribed] = useState(false);
  const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window;
  const key = me?.features.vapid_public_key ?? null;

  useEffect(() => {
    if (!supported) return;
    navigator.serviceWorker.getRegistration().then(
      (reg) => reg?.pushManager.getSubscription().then((s) => setSubscribed(!!s)),
      () => undefined,
    );
  }, [supported]);

  async function enable() {
    setError(null);
    setStatus(null);
    if (!supported || !key) return;
    setBusy(true);
    try {
      // iOS: requestPermission must run straight from the tap.
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        setError("Notifications are blocked. You can allow them in your browser or system settings.");
        return;
      }
      const reg = await swReady();
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(key),
        });
      }
      const json = sub.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) throw new Error("The browser returned an incomplete subscription.");
      await api.pushSubscribe({ endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } });
      setSubscribed(true);
      setStatus("Reminders are on for this device.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setError(null);
    setBusy(true);
    try {
      const reg = await swReady();
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await api.pushUnsubscribe(sub.endpoint);
        await sub.unsubscribe();
      }
      setSubscribed(false);
      setStatus("Reminders are off for this device.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setError(null);
    setBusy(true);
    try {
      const r = await api.pushTest();
      setStatus(r.sent > 0 ? `Sent to ${r.sent} device${r.sent === 1 ? "" : "s"}.` : "No devices are subscribed yet.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Notifications">
      {!me?.features.push || !key ? (
        <p className="muted small">Push notifications aren't set up on this server.</p>
      ) : !supported ? (
        <p className="muted small">
          {isIOS() && !isStandalone()
            ? "On iPhone, add Daybook to your Home Screen (Share → Add to Home Screen), then open it from there to turn on reminders."
            : "This browser doesn't support push notifications."}
        </p>
      ) : (
        <div className="row wrap gap">
          {subscribed ? (
            <button className="btn" onClick={() => void disable()} disabled={busy}>
              Turn off on this device
            </button>
          ) : (
            <button className="btn btn-primary" onClick={() => void enable()} disabled={busy}>
              <Bell size={16} aria-hidden="true" /> Enable reminders
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => void test()} disabled={busy}>
            Send test
          </button>
        </div>
      )}
      {status && <p className="small">{status}</p>}
      {error && <p className="error small">{error}</p>}
    </Section>
  );
}

function DeleteSection({ onDeleted }: { onDeleted: () => void }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (confirm !== "delete") return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteAllData();
      onDeleted();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Section title="Delete all data">
      <form className="stack-sm" onSubmit={submit}>
        <p className="small">
          This permanently deletes every reflection, memory and version. Export first if you want a copy.
        </p>
        <label className="field">
          <span>Type “delete” to confirm</span>
          <input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
        </label>
        <button className="btn btn-danger" type="submit" disabled={busy || confirm !== "delete"}>
          Delete everything
        </button>
        {error && <p className="error small">{error}</p>}
      </form>
    </Section>
  );
}
