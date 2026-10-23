import { useEffect, useState } from "react";
import type { StepOutcome } from "@/lib/toolSteps";

/** What happened during a chat turn, as the console saw it. The chat stream
 *  records these as they arrive; the robot and the thinking indicator read them
 *  to show what the agent is doing right now. Nothing here is invented: each
 *  event matches something the server sent or something the operator did. */
export interface TurnEvent {
  ts: string;
  event: "user_message" | "tool" | "assistant_reply" | "error" | "stopped";
  name?: string;
  answer?: string;
  result?: string;
  outcome?: StepOutcome;
  text?: string;
}

const BUFFER_MAX = 200;
const shared = {
  buffer: [] as TurnEvent[],
  subscribers: new Set<(event: TurnEvent) => void>(),
};

export function recordTurnEvent(event: Omit<TurnEvent, "ts">): void {
  const stamped = { ...event, ts: new Date().toISOString() };
  shared.buffer = [...shared.buffer, stamped].slice(-BUFFER_MAX);
  shared.subscribers.forEach((notify) => notify(stamped));
}

/** True for a tool step the server reported as failed. */
export function isFailedToolEvent(event: TurnEvent): boolean {
  return event.event === "tool" && event.outcome === "failed";
}

/** True for a tool step the server reported as waiting for the operator. */
export function isWaitingToolEvent(event: TurnEvent): boolean {
  return event.event === "tool" && event.outcome === "waiting";
}

/** The most recent turn events, kept up to date as new ones arrive. */
export function useTurnEvents(maxEvents = 50): { events: TurnEvent[] } {
  const [events, setEvents] = useState<TurnEvent[]>(() => shared.buffer.slice(-maxEvents));
  useEffect(() => {
    const onEvent = (event: TurnEvent) =>
      setEvents((prev) => [...prev, event].slice(-maxEvents));
    shared.subscribers.add(onEvent);
    return () => {
      shared.subscribers.delete(onEvent);
    };
  }, [maxEvents]);
  return { events };
}
