import { motion } from "framer-motion";
import type { FeedEvent } from "@/lib/types";
import { formatTime } from "@/lib/format";

interface Props { event: FeedEvent; }

const KIND_COLOR: Record<string, string> = {
  user_message: "var(--color-text)",
  assistant_reply: "var(--color-accent)",
  tool_call: "var(--color-text-dim)",
  tool_result: "var(--color-text-muted)",
  llm_call: "var(--color-warning)",
};

const KIND_LABEL: Record<string, string> = {
  user_message: "message",
  assistant_reply: "reply",
  tool_call: "tool call",
  tool_result: "tool result",
  llm_call: "llm call",
};

export function FeedRow({ event: e }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.25 }}
      className="grid grid-cols-[68px_80px_120px_1fr] gap-5 py-2 items-baseline"
      style={{ borderBottom: "1px solid var(--color-border)" }}
    >
      <span className="label tabular">{formatTime(e.ts)}</span>
      <span className="label">{e.service}</span>
      <span
        className="label"
        style={{ color: KIND_COLOR[e.event] ?? "var(--color-text-muted)" }}
      >
        {KIND_LABEL[e.event] ?? e.event}{e.name ? ` · ${e.name}` : ""}
      </span>
      <span
        className="text-[var(--color-text-dim)] whitespace-pre-wrap break-words"
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: 14.5,
          lineHeight: 1.5,
        }}
      >
        {renderEventDetail(e)}
      </span>
    </motion.div>
  );
}

function renderEventDetail(e: FeedEvent): string {
  switch (e.event) {
    case "user_message":     return e.text ?? "";
    case "assistant_reply":  return e.text ?? "";
    case "tool_call":        return e.args ?? "";
    case "tool_result":      return e.result ?? "";
    case "llm_call":         return `${e.model ?? ""} via ${e.host ?? ""}`;
    default:                 return JSON.stringify(e);
  }
}
