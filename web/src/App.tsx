import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { AuthGate } from "@/components/auth/AuthGate";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { useStatusFavicon } from "@/hooks/useStatusFavicon";

/** Favicon mirrors agent state so a background tab still reads
 *  idle/working/error at a glance. Null render — effect only. */
function StatusFavicon() {
  useStatusFavicon();
  return null;
}

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

/** Auto-detect the user's IANA timezone from the browser and tell the server.
 *  Backend persists it; heartbeat / chat-agent / get_current_time all read
 *  from the same place. No env var, no hardcoding — the system learns it
 *  the first time the UI loads. */
function UserTimezoneSync() {
  useEffect(() => {
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!tz) return;
      // Fire-and-forget; failures are silent. The server falls back to
      // its own system local TZ if this never arrives.
      fetch("/api/user-tz", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tz }),
      }).catch(() => undefined);
    } catch {
      // Intl.DateTimeFormat is missing on very old browsers — skip silently.
    }
  }, []);
  return null;
}

/** Capture the user's home location ONCE from the browser, the same way
 *  UserTimezoneSync handles the timezone. The weather tool and heartbeat read
 *  the persisted value — the model never guesses coordinates. If geolocation
 *  is denied/unavailable we stay silent; the brief gracefully omits weather,
 *  and the user can set a city later via Settings. */
function UserLocationSync() {
  useEffect(() => {
    let cancelled = false;
    fetch("/api/user-location")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || d?.location) return; // already set — don't re-prompt
        if (!("geolocation" in navigator)) return;
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            fetch("/api/user-location", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
              }),
            }).catch(() => undefined);
          },
          () => undefined, // denied / unavailable — silent
          { maximumAge: 86_400_000, timeout: 10_000 },
        );
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);
  return null;
}

const LandingPage = lazy(() => import("@/pages/LandingPage").then((m) => ({ default: m.LandingPage })));
const OverviewPage = lazy(() => import("@/pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const ChatPage = lazy(() => import("@/pages/ChatPage").then((m) => ({ default: m.ChatPage })));
const FeedPage = lazy(() => import("@/pages/FeedPage").then((m) => ({ default: m.FeedPage })));
const TasksPage = lazy(() => import("@/pages/TasksPage").then((m) => ({ default: m.TasksPage })));
const EvalsPage = lazy(() => import("@/pages/EvalsPage").then((m) => ({ default: m.EvalsPage })));
const SkillsPage = lazy(() => import("@/pages/SkillsPage").then((m) => ({ default: m.SkillsPage })));
const MemoryPage = lazy(() => import("@/pages/MemoryPage").then((m) => ({ default: m.MemoryPage })));
const MemoryEntryPage = lazy(() => import("@/pages/MemoryEntryPage").then((m) => ({ default: m.MemoryEntryPage })));
const LogsPage = lazy(() => import("@/pages/LogsPage").then((m) => ({ default: m.LogsPage })));
const LogEntryPage = lazy(() => import("@/pages/LogEntryPage").then((m) => ({ default: m.LogEntryPage })));
const GalleryPage = lazy(() => import("@/pages/GalleryPage").then((m) => ({ default: m.GalleryPage })));

function RouteFallback() {
  // Scanline skeleton in the page's own shape (header line + hero panel
  // + readout row) — a chunk load should feel like the CRT warming up,
  // not a bare string.
  return (
    <div
      className="min-h-[calc(100vh-48px)] px-10 pt-10"
      style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
    >
      {/* Explicit signal first — the skeleton alone read as a blank page
          on this dark surface. */}
      <div
        className="text-[10px] uppercase tracking-[0.32em] mb-4"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── loading <span className="hm-tick-sep">▮</span>
      </div>
      <div className="hm-skeleton" style={{ height: 18, width: 220, marginBottom: 28 }} />
      <div className="hm-skeleton" style={{ height: 180, marginBottom: 20 }} />
      <div className="flex gap-4">
        <div className="hm-skeleton" style={{ height: 72, flex: 1 }} />
        <div className="hm-skeleton" style={{ height: 72, flex: 1 }} />
        <div className="hm-skeleton" style={{ height: 72, flex: 1 }} />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <ScrollToTop />
        <UserTimezoneSync />
        <UserLocationSync />
        <StatusFavicon />
        <DashboardShell>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/"         element={<LandingPage />} />
              <Route path="/overview" element={<OverviewPage />} />
              <Route path="/chat"     element={<ChatPage />} />
              {/* /live merged into /traces — same SSE stream, no value in two routes */}
              <Route path="/traces"  element={<FeedPage />} />
              <Route path="/tasks"   element={<TasksPage />} />
              <Route path="/evals"   element={<EvalsPage />} />
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
