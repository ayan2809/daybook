import type { ReactNode } from "react";

export function Loading({ label = "Loading…", full = false }: { label?: string; full?: boolean }) {
  return (
    <div className={full ? "loading loading-full" : "loading"} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "neutral" | "success";
  children: ReactNode;
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}
