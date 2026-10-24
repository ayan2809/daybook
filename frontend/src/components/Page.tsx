import type { ReactNode } from "react";
import { DemoBanner } from "./DemoBanner";
import { TabBar } from "./TabBar";

/** Standard signed-in page: demo banner, content column, bottom tabs. */
export function Page({
  children,
  tabs = true,
  wide = false,
  className = "",
}: {
  children: ReactNode;
  tabs?: boolean;
  wide?: boolean;
  className?: string;
}) {
  return (
    <div className={tabs ? "shell with-tabs" : "shell"}>
      <DemoBanner />
      <main className={`page ${wide ? "page-wide" : ""} ${className}`}>{children}</main>
      {tabs && <TabBar />}
    </div>
  );
}
