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
import { GalleryPage } from "@/pages/GalleryPage";
import { LandingPage } from "@/pages/LandingPage";

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <DashboardShell>
          <Routes>
            <Route path="/"         element={<LandingPage />} />
            <Route path="/overview" element={<OverviewPage />} />
            <Route path="/chat"     element={<ChatPage />} />
            {/* /live merged into /traces — same SSE stream, no value in two routes */}
            <Route path="/traces"  element={<FeedPage />} />
            <Route path="/tasks"   element={<TasksPage />} />
            <Route path="/tools"   element={<SkillsPage />} />
            <Route path="/memory"  element={<MemoryPage />} />
            <Route path="/memory/:filename" element={<MemoryEntryPage />} />
            <Route path="/logs"    element={<LogsPage />} />
            <Route path="/logs/*"  element={<LogEntryPage />} />
            <Route path="/lab"     element={<GalleryPage />} />
          </Routes>
        </DashboardShell>
      </BrowserRouter>
    </AuthGate>
  );
}

