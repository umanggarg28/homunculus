import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { PlanModeBanner } from "./PlanModeBanner";

/** App shell: fixed sidebar on the left, scrolling content on the right. */
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen" style={{ background: "var(--color-bg)" }}>
      <Sidebar />
      <main style={{ marginLeft: 220, minHeight: "100vh" }}>
        <PlanModeBanner />
        {children}
      </main>
    </div>
  );
}
