import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthGate } from "@/components/auth/AuthGate";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { OverviewPage } from "@/pages/OverviewPage";
import { ChatPage } from "@/pages/ChatPage";
import { FeedPage } from "@/pages/FeedPage";
import { TasksPage } from "@/pages/TasksPage";
import { SkillsPage } from "@/pages/SkillsPage";
import { MemoryPage } from "@/pages/MemoryPage";
import { MemoryEntryPage } from "@/pages/MemoryEntryPage";
import { LogsPage } from "@/pages/LogsPage";
import { LogEntryPage } from "@/pages/LogEntryPage";

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <DashboardShell>
          <Routes>
            <Route path="/"        element={<OverviewPage />} />
            <Route path="/chat"    element={<ChatPage />} />
            <Route path="/live"    element={<LivePagePlaceholder />} />
            <Route path="/traces"  element={<FeedPage />} />
            <Route path="/tasks"   element={<TasksPage />} />
            <Route path="/skills"  element={<SkillsPage />} />
            <Route path="/memory"  element={<MemoryPage />} />
            <Route path="/memory/:filename" element={<MemoryEntryPage />} />
            <Route path="/logs"    element={<LogsPage />} />
            <Route path="/logs/*"  element={<LogEntryPage />} />
          </Routes>
        </DashboardShell>
      </BrowserRouter>
    </AuthGate>
  );
}

function LivePagePlaceholder() {
  return (
    <div className="max-w-[1200px] mx-auto px-8 pt-10 pb-16">
      <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
        Live
      </div>
      <h1 className="text-[var(--color-text)] mb-3" style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600 }}>
        The agent's computer
      </h1>
      <p className="text-[14px] text-[var(--color-text-muted)] max-w-[560px]">
        Real-time view of what the agent is doing this second — tools in flight, model
        thinking, memory loading. Coming in the next session.
      </p>
    </div>
  );
}
