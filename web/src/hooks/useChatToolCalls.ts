import { useEffect, useState } from "react";
import { useEventStream } from "./useEventStream";
import type { ToolCallEntry } from "@/components/chat/ToolCallCard";

/** Pair `tool_call` + `tool_result` events from the `feed` service into
 * a timeline. We reset the list when a fresh `user_message` event
 * arrives from the feed service — that marks the start of a new turn.
 *
 * Returns tool calls in order, including any still in-flight (no
 * result yet). Caller renders them inline in the chat. */
export function useChatToolCalls(): ToolCallEntry[] {
  const { events } = useEventStream(120);
  const [calls, setCalls] = useState<ToolCallEntry[]>([]);

  useEffect(() => {
    // Walk events from oldest to newest, restart the list on each
    // user_message (= new chat turn from the feed service).
    const result: ToolCallEntry[] = [];
    for (const e of events) {
      if (e.service !== "feed") continue;
      if (e.event === "user_message") {
        result.length = 0;
        continue;
      }
      if (e.event === "tool_call" && e.name) {
        result.push({
          name: e.name,
          args: e.args ?? "",
          startedAt: new Date(e.ts).getTime(),
        });
      } else if (e.event === "tool_result" && e.name) {
        // Match to the most recent open (no result) call with the same name.
        for (let i = result.length - 1; i >= 0; i--) {
          if (result[i].name === e.name && result[i].result === undefined) {
            const endedAt = new Date(e.ts).getTime();
            result[i] = {
              ...result[i],
              result: e.result ?? "",
              durationMs: endedAt - result[i].startedAt,
            };
            break;
          }
        }
      } else if (e.event === "assistant_reply") {
        // Final reply means all tools for this turn are done; freeze list.
        // (We just don't reset.)
      }
    }
    setCalls(result);
  }, [events]);

  return calls;
}
