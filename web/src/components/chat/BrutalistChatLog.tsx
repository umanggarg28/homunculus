import { Fragment, useMemo } from "react";
import type { ChatMessage } from "@/hooks/useChatStream";
import type { ToolCallEntry } from "./ToolCallCard";
import { BrutalistMessage } from "./BrutalistMessage";
import { BrutalistLanding } from "./BrutalistLanding";
import { useAutoScroll } from "@/hooks/useAutoScroll";

interface Props {
  messages: ChatMessage[];
  toolTimeline: ToolCallEntry[];
  sending: boolean;
  bootDone: boolean;
  onPickPrompt: (text: string) => void;
}

/** The brutalist chat surface: a single column of prompt rows
 *  and reasoning lines, with KEY|VAL|STATUS tool blocks between
 *  user turn and final agent reply. */
export function BrutalistChatLog({ messages, toolTimeline, sending, bootDone, onPickPrompt }: Props) {
  // Tool calls belong to the most recent assistant message — for
  // earlier turns we only have the message text. (Same approach the
  // old ChatLog took.)
  const assistantToolCalls = useMemo(() => {
    const map = new Map<string, ToolCallEntry[]>();
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant) map.set(lastAssistant.id, toolTimeline);
    return map;
  }, [messages, toolTimeline]);

  // Trigger auto-scroll on the streaming content's growing length. Using
  // the last message's character count means we scroll on every chunk
  // during streaming, not just message-count changes.
  const scrollTrigger = useMemo(() => {
    const last = messages[messages.length - 1];
    const lastLen = (last?.content ?? "").length;
    return `${messages.length}:${lastLen}:${toolTimeline.length}:${sending ? 1 : 0}`;
  }, [messages, toolTimeline, sending]);
  useAutoScroll(scrollTrigger);

  if (messages.length === 0) {
    return <BrutalistLanding bootDone={bootDone} onPick={onPickPrompt} />;
  }

  // Turn numbering = number of user messages so far + 1 for the
  // current assistant reply that belongs to the same turn.
  let turn = 0;
  return (
    <div className="flex flex-col gap-3">
      {messages.map((m) => {
        if (m.role === "user") turn += 1;
        const tools = (m.role === "assistant" && assistantToolCalls.get(m.id)) || [];
        return (
          <Fragment key={m.id}>
            <BrutalistMessage message={m} toolCalls={tools} sending={sending} turnNumber={turn} />
          </Fragment>
        );
      })}
    </div>
  );
}
