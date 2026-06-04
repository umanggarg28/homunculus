import { useMemo } from "react";
import { motion } from "framer-motion";
import { MarkdownMessage } from "./MarkdownMessage";
import { BrutalistToolBlock } from "./BrutalistToolBlock";
import { ThinkingIndicator } from "./ThinkingIndicator";
import type { ChatMessage } from "@/hooks/useChatStream";
import type { ToolCallEntry } from "./ToolCallCard";

interface Props {
  message: ChatMessage;
  toolCalls: ToolCallEntry[];
  sending: boolean;
  turnNumber: number;
}

/** One chat turn with a left data-rail.
 *
 *  ┌── 62px ─────────────┬── content ──────────────────┐
 *  │  YOU           │  user@homunculus:~$ <text>   │
 *  │  18:42         │                              │
 *  └─────────────────────┴──────────────────────────────┘
 *
 *  Agent replies use MarkdownMessage (full rehype-highlight rendering).
 *  Entrance: each turn slides up 6px and fades in via framer-motion.
 */
export function BrutalistMessage({ message, toolCalls, sending }: Props) {
  const isUser = message.role === "user";
  const timeStr = useMemo(() => fmtTime(message.id), [message.id]);
  const inFlight = message.inFlight ?? false;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="my-5"
      style={{
        display: "grid",
        gridTemplateColumns: "62px minmax(0, 1fr)",
        columnGap: "20px",
        alignItems: "start",
      }}
    >
      {/* DATA RAIL */}
      <div
        className="text-[10px] leading-[1.5] pt-[2px] select-none"
        style={{
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
          textAlign: "right",
          paddingRight: "12px",
          borderRight: isUser
            ? "1px solid var(--color-border)"
            : "1px solid var(--color-border-strong)",
          color: "var(--color-text-faint)",
        }}
      >
        <div
          className="uppercase tracking-[0.16em] mb-1.5"
          style={{
            color: isUser ? "var(--color-accent)" : "var(--color-text-muted)",
            letterSpacing: "0.18em",
          }}
        >
          {isUser ? "you" : "ai"}
        </div>
        <div className="uppercase tracking-[0.08em]">
          {inFlight && !isUser
            ? <span style={{ color: "var(--color-amber)" }}>live</span>
            : timeStr}
        </div>
        {message.source && message.source !== "web" && (
          <div
            className="mt-1 uppercase tracking-[0.12em]"
            style={{ color: "var(--color-text-muted)", fontSize: 9 }}
            title={`via ${message.source}`}
          >
            via {message.source === "telegram" ? "tg" : message.source}
          </div>
        )}
        {!isUser && toolCalls.length > 0 && !inFlight && (
          <div className="mt-1.5 uppercase tracking-[0.06em]" style={{ color: "var(--color-text-faint)" }}>
            ↳{toolCalls.length}T
          </div>
        )}
      </div>

      {/* CONTENT */}
      <div style={{ minWidth: 0 }}>
        {isUser ? (
          <UserPrompt content={message.content} />
        ) : (
          <AgentReply
            message={message}
            toolCalls={toolCalls}
            inFlight={inFlight}
            sending={sending}
          />
        )}
      </div>
    </motion.div>
  );
}

function UserPrompt({ content }: { content: string }) {
  return (
    <div
      className="text-[13px] leading-[1.6]"
      style={{ fontFamily: "var(--font-mono)", paddingTop: 1 }}
    >
      <span
        className="select-none"
        style={{ color: "var(--color-accent)", marginRight: 8 }}
      >
        user@homunculus:~$
      </span>
      <span
        style={{
          color: "var(--color-text)",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        {content}
      </span>
    </div>
  );
}

interface AgentReplyProps {
  message: ChatMessage;
  toolCalls: ToolCallEntry[];
  inFlight: boolean;
  sending: boolean;
}

function AgentReply({ message, toolCalls, inFlight, sending }: AgentReplyProps) {
  const isWorking = inFlight && sending;
  return (
    <div>
      {/* ThinkingIndicator: robot face + live trace while active;
          collapses to "▸ thought for Xs · N tools" when the turn completes */}
      <ThinkingIndicator active={isWorking} />

      {/* Tool call receipts (legacy path, when toolCalls prop is populated) */}
      {toolCalls.length > 0 && (
        <div className="mb-4 mt-2">
          {toolCalls.map((tc, i) => (
            <BrutalistToolBlock key={i} entry={tc} />
          ))}
        </div>
      )}

      {/* Reply content — only once text starts streaming in */}
      {message.content && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "16px minmax(0, 1fr)",
            columnGap: "10px",
            alignItems: "first baseline",
            marginTop: 6,
          }}
        >
          <span
            className="select-none"
            style={{ color: "var(--color-accent)", fontSize: 13, lineHeight: "1.7" }}
          >
            ›
          </span>
          <div style={{ minWidth: 0 }}>
            <MarkdownMessage text={message.content} />
            {isWorking && <Cursor />}
          </div>
        </div>
      )}
    </div>
  );
}


function Cursor() {
  return (
    <motion.span
      animate={{ opacity: [1, 0] }}
      transition={{ duration: 0.55, repeat: Infinity, ease: "linear" }}
      style={{
        display: "inline-block",
        width: "7px",
        height: "1em",
        background: "var(--color-accent)",
        verticalAlign: "text-bottom",
        marginLeft: 3,
      }}
    />
  );
}

function fmtTime(id: string) {
  const n = parseInt(id.replace(/[^0-9]/g, "").slice(0, 13), 10);
  const d = Number.isFinite(n) && n > 1_000_000_000_000 ? new Date(n) : new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
