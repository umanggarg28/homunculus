import { createContext, createElement, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Approval } from "@/lib/api";
import { useTurnEvents } from "@/lib/turnEvents";

// Requests appear, and follow-up replies land, while the operator may be on another
// screen, so the list is refreshed on a timer as well as after every turn event.
const REFRESH_MS = 10_000;

interface ApprovalsState {
  approvals: Approval[];
  pendingCount: number;
  refresh: () => Promise<void>;
}

const ApprovalsContext = createContext<ApprovalsState>({
  approvals: [],
  pendingCount: 0,
  refresh: async () => undefined,
});

/** Keeps one approvals list from the server for the whole console, so the sidebar
 *  badge and the approvals screen always show the same requests. */
export function ApprovalsProvider({ children }: { children: ReactNode }) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const { events } = useTurnEvents(1);

  const refresh = useCallback(async () => {
    try {
      setApprovals(await api.approvals());
    } catch {
      // Unreachable server: keep the last list rather than showing an empty one.
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (events.length) refresh();
  }, [events, refresh]);

  const pendingCount = approvals.filter((a) => a.status === "pending").length;
  return createElement(ApprovalsContext.Provider, { value: { approvals, pendingCount, refresh } }, children);
}

/** The approvals list shared by everything inside ApprovalsProvider. */
export function useApprovals(): ApprovalsState {
  return useContext(ApprovalsContext);
}
