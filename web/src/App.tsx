import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { ActivityPage } from "@/pages/ActivityPage";
import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { ChatPage } from "@/pages/ChatPage";

/** Each route is its own surface, so a new route starts at the top. */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <DashboardShell>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </DashboardShell>
    </BrowserRouter>
  );
}
