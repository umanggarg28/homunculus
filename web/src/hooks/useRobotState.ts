import { useEffect, useState } from "react";
import { useEventStream } from "./useEventStream";
import type { RobotState } from "@/components/robot/HomunculusRobot";

/** Map the agent's real activity (SSE events) to the robot's state
 *  machine. Returns the current state the robot should be in.
 *
 *  Mapping:
 *    - llm_call         → thinking
 *    - tool_call        → working
 *    - tool_result OK   → working (continue) or success briefly
 *    - tool_result ERR  → error
 *    - assistant_reply  → responding, then success
 *    - silence >  5s    → idle
 *    - first load <3s   → boot
 */
export function useRobotState(options?: { bootDurationMs?: number }): RobotState {
  const { events } = useEventStream(80);
  const [state, setState] = useState<RobotState>("boot");
  const bootMs = options?.bootDurationMs ?? 2700;

  // initial boot
  useEffect(() => {
    const t = setTimeout(() => {
      setState((prev) => (prev === "boot" ? "idle" : prev));
    }, bootMs);
    return () => clearTimeout(t);
  }, [bootMs]);

  useEffect(() => {
    if (state === "boot") return;
    const last = events[events.length - 1];
    if (!last) return;
    const ageMs = Date.now() - new Date(last.ts).getTime();
    if (ageMs > 8000) {
      setState("idle");
      return;
    }
    const isErr = last.event === "tool_result" && typeof last.result === "string" && last.result.startsWith("ERROR");
    if (isErr) {
      setState("error");
      const t = setTimeout(() => setState("idle"), 2400);
      return () => clearTimeout(t);
    }
    if (last.event === "tool_call") setState("working");
    else if (last.event === "tool_result") setState("working");
    else if (last.event === "llm_call") setState("thinking");
    else if (last.event === "assistant_reply") {
      setState("responding");
      const t = setTimeout(() => setState("success"), 1500);
      const t2 = setTimeout(() => setState("idle"), 3000);
      return () => { clearTimeout(t); clearTimeout(t2); };
    } else if (last.event === "user_message") {
      setState("listening");
    }
  }, [events, state]);

  // idle-on-silence: if no event in 10s, go idle
  useEffect(() => {
    if (state === "boot" || state === "idle") return;
    const last = events[events.length - 1];
    const lastTs = last ? new Date(last.ts).getTime() : 0;
    const idleAfter = setTimeout(() => {
      if (Date.now() - lastTs > 9000) setState("idle");
    }, 10_000);
    return () => clearTimeout(idleAfter);
  }, [state, events]);

  return state;
}
