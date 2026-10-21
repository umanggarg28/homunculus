import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { CommandPalette } from "./CommandPalette";
import { BootSequence } from "./BootSequence";
import { Atmosphere } from "./Atmosphere";
import { RobotStateProvider } from "@/hooks/useRobotState";

/** App shell: fixed sidebar on the left, scrolling content on the right. */
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <RobotStateProvider>
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
        <Sidebar />
        <main className="dashboard-main">{children}</main>
        <CommandPalette />
      </div>
    </RobotStateProvider>
  );
}
