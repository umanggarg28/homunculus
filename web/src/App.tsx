import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApprovalsProvider } from "@/hooks/useApprovals";
import { AgentStateProvider } from "@/state/agentState";
import { Shell } from "@/shell/Shell";
import { ActivityPage } from "@/pages/ActivityPage";

/** One screen at "/". The earlier addresses (/chat, /approvals, /activity) lead to it,
 *  so old links and bookmarks still work. */
export default function App() {
  return (
    <BrowserRouter>
      <ApprovalsProvider>
        <AgentStateProvider>
          <Routes>
            <Route path="/" element={<Shell activity={<ActivityPage />} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AgentStateProvider>
      </ApprovalsProvider>
    </BrowserRouter>
  );
}
