// Google Identity Services (the "Sign in with Google" button). Loaded only on the landing page.
// Docs: developers.google.com/identity/gsi/web/reference/js-reference

interface CredentialResponse {
  credential: string;
}

interface GoogleId {
  initialize(config: {
    client_id: string;
    callback: (r: CredentialResponse) => void;
    ux_mode?: "popup" | "redirect";
    auto_select?: boolean;
    cancel_on_tap_outside?: boolean;
    context?: "signin" | "signup" | "use";
  }): void;
  renderButton(parent: HTMLElement, options: Record<string, string | number>): void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

const SRC = "https://accounts.google.com/gsi/client";
let loading: Promise<GoogleId> | null = null;

export function loadGoogle(): Promise<GoogleId> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SRC;
    s.async = true;
    s.onload = () => (window.google ? resolve(window.google.accounts.id) : reject(new Error("no google")));
    s.onerror = () => {
      loading = null;
      reject(new Error("Couldn't load Google sign-in."));
    };
    document.head.appendChild(s);
  });
  return loading;
}
