import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApprovalsProvider } from "@/hooks/useApprovals";
import { AgentStateProvider } from "@/state/agentState";
import { Shell } from "@/shell/Shell";
import { MemoryScreen } from "@/memory/MemoryScreen";

/** Now at "/", and Memory under "/memory". Any other address, including the earlier
 *  /chat, /approvals and /activity, leads to Now, so old links and bookmarks still work. */
export default function App() {
  return (
    <BrowserRouter>
      <ApprovalsProvider>
        <AgentStateProvider>
          <Routes>
            <Route path="/" element={<Shell />} />
            <Route path="/memory/*" element={<MemoryScreen />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AgentStateProvider>
      </ApprovalsProvider>
    </BrowserRouter>
  );
}
