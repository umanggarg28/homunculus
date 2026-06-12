import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { PlanModeBanner } from "./PlanModeBanner";
import { AlertBanner } from "./AlertBanner";
import { CommandPalette } from "./CommandPalette";
import { BootSequence } from "./BootSequence";
import { Atmosphere } from "./Atmosphere";
import { AutonomousFrame } from "./AutonomousFrame";

/** App shell: fixed sidebar on the left, scrolling content on the right. */
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen" style={{ background: "var(--color-bg)" }}>
      <style>{`
        .dashboard-main {
          margin-left: 220px;
          min-height: 100vh;
        }
        @media (max-width: 760px) {
          .dashboard-main {
            margin-left: 0;
            padding-top: 58px;
          }
        }
      `}</style>
      <Atmosphere />
      <BootSequence />
      <AutonomousFrame />
      <Sidebar />
      <main className="dashboard-main">
        <AlertBanner />
        <PlanModeBanner />
        {children}
      </main>
      <CommandPalette />
    </div>
  );
}
