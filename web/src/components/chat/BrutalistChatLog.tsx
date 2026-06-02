import { Fragment, useMemo } from "react";
import { motion } from "framer-motion";
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
  historyLoading: boolean;
  onPickPrompt: (text: string) => void;
}

/** The brutalist chat surface: a single column of prompt rows
 *  and reasoning lines, with KEY|VAL|STATUS tool blocks between
 *  user turn and final agent reply. */
export function BrutalistChatLog({ messages, toolTimeline, sending, bootDone, historyLoading, onPickPrompt }: Props) {
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

  if (historyLoading) {
    return <SessionLoader />;
  }

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

function SessionLoader() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: 220,
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.8,
          color: "var(--color-text-dim)",
          padding: "18px 24px",
          border: "1px solid var(--color-border)",
          background: "rgba(0,0,0,0.35)",
          minWidth: 280,
        }}
      >
        <div style={{ color: "var(--color-accent)" }}>$ homunculus --restore-session</div>
        <div>
          <span style={{ color: "var(--color-accent)" }}>[OK]</span>
          <span> loading session history</span>
        </div>
        <div style={{ marginTop: 4 }}>
          <span style={{ color: "var(--color-text-muted)" }}>&gt; </span>
          <motion.span
            animate={{ opacity: [1, 0] }}
            transition={{ duration: 0.55, repeat: Infinity, ease: "linear" }}
            style={{
              display: "inline-block",
              width: 8,
              height: 13,
              background: "var(--color-accent)",
              verticalAlign: "middle",
            }}
          />
        </div>
      </div>
    </motion.div>
  );
}
