import { createContext, createElement, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { isFailedToolEvent, useTurnEvents } from "@/lib/turnEvents";
import type { RobotState } from "@/components/robot/HomunculusRobot";

/** Map what is happening in the current chat turn to the robot's state.
 *  It is computed once, in RobotStateProvider, and read with useRobotState,
 *  so every place that shows the state shows the same one.
 *
 *  Each new event moves the robot once:
 *    - user_message      → thinking
 *    - tool step         → working, or error if the step failed
 *    - assistant_reply   → responding, then success, then idle
 *    - error             → error, then idle
 *    - stopped           → idle
 *  While no turn is in progress, ten seconds of silence returns it to idle.
 */
function useComputedRobotState(options?: { bootDurationMs?: number }): RobotState {
  const { events } = useTurnEvents(80);
  const [state, setState] = useState<RobotState>("boot");
  const bootMs = options?.bootDurationMs ?? 2700;
  const handledRef = useRef<string>("");
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const later = (ms: number, next: RobotState) => {
    timersRef.current.push(setTimeout(() => setState(next), ms));
  };
  const clearTimers = () => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
  };

  useEffect(() => {
    const t = setTimeout(() => setState((prev) => (prev === "boot" ? "idle" : prev)), bootMs);
    return () => clearTimeout(t);
  }, [bootMs]);

  useEffect(() => clearTimers, []);

  useEffect(() => {
    const last = events[events.length - 1];
    if (!last) return;
    const key = `${last.ts}|${last.event}|${last.name ?? ""}`;
    if (key === handledRef.current) return;
    handledRef.current = key;
    clearTimers();

    if (last.event === "error" || isFailedToolEvent(last)) {
      setState("error");
      later(2400, "idle");
    } else if (last.event === "tool") {
      setState("working");
    } else if (last.event === "user_message") {
      setState("thinking");
    } else if (last.event === "stopped") {
      setState("idle");
    } else if (last.event === "assistant_reply") {
      setState("responding");
      later(1500, "success");
      later(3000, "idle");
    }
    // A turn that ends without a reply or an error, for example when it is
    // stopped, should not leave the robot busy forever.
    const turnInProgress = last.event === "user_message" || last.event === "tool";
    if (!turnInProgress) later(10_000, "idle");
  }, [events]);

  return state;
}

const RobotStateContext = createContext<RobotState>("boot");

export function RobotStateProvider({ children }: { children: ReactNode }) {
  return createElement(RobotStateContext.Provider, { value: useComputedRobotState() }, children);
}

/** The robot's current state, shared by everything inside RobotStateProvider. */
export function useRobotState(): RobotState {
  return useContext(RobotStateContext);
}
