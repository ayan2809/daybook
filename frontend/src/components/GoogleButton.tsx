import { useEffect, useRef, useState } from "react";
import { loadGoogle } from "../lib/google";

/** Google's own button (their branding rules require it). Calls onCredential with the ID token. */
export function GoogleButton({
  clientId,
  onCredential,
}: {
  clientId: string;
  onCredential: (credential: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  cb.current = onCredential;
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadGoogle().then(
      (id) => {
        if (cancelled || !box.current) return;
        id.initialize({
          client_id: clientId,
          callback: (r) => cb.current(r.credential),
          ux_mode: "popup",
          auto_select: false,
          context: "signin",
        });
        id.renderButton(box.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "continue_with",
          shape: "pill",
          logo_alignment: "center",
          width: Math.min(box.current.clientWidth || 320, 400),
        });
      },
      (err: Error) => !cancelled && setFailed(err.message),
    );
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (failed) return <p className="error small">{failed}</p>;
  return <div ref={box} className="google-button" />;
}
