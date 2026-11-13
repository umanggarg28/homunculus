import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from "react";
import { useApprovals } from "@/hooks/useApprovals";
import { useTurnEvents, type TurnEvent } from "@/lib/turnEvents";

/** What the agent is doing now, as one word the whole console agrees on. The status
 *  pill, the spoken line and the presence all read it, so they can never disagree.
 *
 *  - ready:    nothing running and nothing waiting on the operator
 *  - thinking: a turn has started and no tool has run yet
 *  - working:  a turn is running and at least one tool step has happened
 *  - waiting:  no turn is running and a request needs the operator
 *  - done:     the last turn just finished with a reply (a short moment, then ready)
 *  - stuck:    the last turn ended with an error
 *  - stopped:  the operator stopped the last turn
 */
export type AgentKind = "ready" | "thinking" | "working" | "waiting" | "done" | "stuck" | "stopped";

export interface AgentState {
  kind: AgentKind;
  /** Tool steps so far in the current or last turn. There is no total: the agent does
   *  not plan ahead, so the console never claims to know how many steps are left. */
  step: number;
  /** The latest tool step of the current or last turn. */
  last: TurnEvent | null;
  /** When the current or last turn started, as the console saw it. */
  since: string | null;
  /** The error the last turn ended with. */
  error: string | null;
  /** How many requests are waiting on the operator. */
  pending: number;
  /** The oldest of those requests, when it was made. */
  pendingSince: string | null;
}

// How long "done" lasts before the agent counts as ready again.
export const DONE_MS = 6_000;

/** Work out the state from the turn events and the waiting requests. Pure, so the same
 *  events always give the same answer. */
export function deriveAgentState(
  events: TurnEvent[],
  pending: { created: string }[],
  now: number,
): AgentState {
  const start = events.map((e) => e.event).lastIndexOf("user_message");
  const turn = start === -1 ? [] : events.slice(start);
  const tools = turn.filter((e) => e.event === "tool");
  const end = turn.find((e) => e.event === "assistant_reply" || e.event === "error" || e.event === "stopped");
  const oldest = pending.map((p) => p.created).sort()[0] ?? null;
  const base = {
    step: tools.length,
    last: tools[tools.length - 1] ?? null,
    since: turn[0]?.ts ?? null,
    error: end?.event === "error" ? (end.text ?? null) : null,
    pending: pending.length,
    pendingSince: oldest,
  };
  if (start !== -1 && !end) return { ...base, kind: tools.length ? "working" : "thinking" };
  if (pending.length) return { ...base, kind: "waiting" };
  if (!end) return { ...base, kind: "ready" };
  if (end.event === "error") return { ...base, kind: "stuck" };
  if (end.event === "stopped") return { ...base, kind: "stopped" };
  return { ...base, kind: now - Date.parse(end.ts) < DONE_MS ? "done" : "ready" };
}

const EMPTY: AgentState = { kind: "ready", step: 0, last: null, since: null, error: null, pending: 0, pendingSince: null };
const AgentContext = createContext<AgentState>(EMPTY);

/** Computes the agent's state once for the whole console. */
export function AgentStateProvider({ children }: { children: ReactNode }) {
  const { events } = useTurnEvents(200);
  const { approvals } = useApprovals();
  const pending = approvals.filter((a) => a.status === "pending");
  const [now, setNow] = useState(() => Date.now());
  const state = deriveAgentState(events, pending, now);
  // "done" ends by itself, so wake once when it is due to.
  useEffect(() => {
    if (state.kind !== "done") return;
    const end = events[events.length - 1];
    const wait = end ? Math.max(0, DONE_MS - (Date.now() - Date.parse(end.ts))) + 50 : DONE_MS;
    const timer = setTimeout(() => setNow(Date.now()), wait);
    return () => clearTimeout(timer);
  }, [state.kind, events]);
  // A new event is a new moment: measure "done" against it.
  useEffect(() => setNow(Date.now()), [events]);
  return createElement(AgentContext.Provider, { value: state }, children);
}

/** The agent's state, shared by everything inside AgentStateProvider. */
export function useAgentState(): AgentState {
  return useContext(AgentContext);
}
