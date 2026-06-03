import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { AuthGate } from "@/components/auth/AuthGate";
import { DashboardShell } from "@/components/layout/DashboardShell";

/** Reset scroll on every route change. React Router preserves scroll
 *  position across navigation; for a multi-page app where each route is
 *  its own surface, this is unwanted — e.g. Chat scrolls to the bottom
 *  as messages arrive, then nav inherits that scroll on the next page.
 *  Component pattern: live inside <BrowserRouter>, listen to pathname. */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  return null;
}

const LandingPage = lazy(() => import("@/pages/LandingPage").then((m) => ({ default: m.LandingPage })));
const OverviewPage = lazy(() => import("@/pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const ChatPage = lazy(() => import("@/pages/ChatPage").then((m) => ({ default: m.ChatPage })));
const FeedPage = lazy(() => import("@/pages/FeedPage").then((m) => ({ default: m.FeedPage })));
const TasksPage = lazy(() => import("@/pages/TasksPage").then((m) => ({ default: m.TasksPage })));
const SkillsPage = lazy(() => import("@/pages/SkillsPage").then((m) => ({ default: m.SkillsPage })));
const MemoryPage = lazy(() => import("@/pages/MemoryPage").then((m) => ({ default: m.MemoryPage })));
const MemoryEntryPage = lazy(() => import("@/pages/MemoryEntryPage").then((m) => ({ default: m.MemoryEntryPage })));
const LogsPage = lazy(() => import("@/pages/LogsPage").then((m) => ({ default: m.LogsPage })));
const LogEntryPage = lazy(() => import("@/pages/LogEntryPage").then((m) => ({ default: m.LogEntryPage })));
const GalleryPage = lazy(() => import("@/pages/GalleryPage").then((m) => ({ default: m.GalleryPage })));

function RouteFallback() {
  return (
    <div
      className="min-h-[calc(100vh-48px)] px-10 pt-10 brut-meta"
      style={{ color: "var(--color-text-muted)", background: "var(--color-bg)" }}
    >
      loading route...
    </div>
  );
}

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <ScrollToTop />
        <DashboardShell>
          <Suspense fallback={<RouteFallback />}>
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
          </Suspense>
        </DashboardShell>
      </BrowserRouter>
    </AuthGate>
  );
}
