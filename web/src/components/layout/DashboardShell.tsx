import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { PlanModeBanner } from "./PlanModeBanner";
import { AlertBanner } from "./AlertBanner";
import { CommandPalette } from "./CommandPalette";
import { BootSequence } from "./BootSequence";
import { Atmosphere } from "./Atmosphere";
import { SoundToggle } from "./SoundToggle";

/** App shell: fixed sidebar on the left, scrolling content on the right. */
export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen" style={{ background: "var(--color-bg)" }}>
      <Atmosphere />
      <BootSequence />
      <Sidebar />
      <main style={{ marginLeft: 220, minHeight: "100vh" }}>
        <AlertBanner />
        <PlanModeBanner />
        {children}
      </main>
      <CommandPalette />
      <SoundToggle />
    </div>
  );
}
