import { motion } from "framer-motion";
import { MarkdownMessage } from "./MarkdownMessage";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { ManuscriptMargin } from "./ManuscriptMargin";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatStream";
import type { ToolCallEntry } from "./ToolCallCard";

interface Props {
  message: ChatMessageType;
  toolCalls: ToolCallEntry[];
}

export function ChatMessage({ message, toolCalls }: Props) {
  const isUser = message.role === "user";

  return (
    <motion.article
      initial={{ opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="grid grid-cols-[60px_1fr] gap-4"
    >
      <div className="pt-0.5 text-right">
        <span
          className="text-[11px] font-medium uppercase tracking-wider"
          style={{
            color: isUser ? "var(--color-accent)" : "var(--color-text-muted)",
          }}
        >
          {isUser ? "You" : "Agent"}
        </span>
      </div>
      <div className="min-w-0">
        <div
          className="prose"
          style={{
            color: isUser ? "var(--color-text-dim)" : "var(--color-text)",
          }}
        >
          {isUser ? (
            <span style={{ whiteSpace: "pre-wrap" }}>{message.content}</span>
          ) : (
            <MarkdownMessage text={message.content} />
          )}
        </div>
        {!isUser && toolCalls.length > 0 && (
          <div className="mt-3"><ManuscriptMargin entries={toolCalls} /></div>
        )}
        {!isUser && <ThinkingIndicator active={!!message.inFlight} />}
      </div>
    </motion.article>
  );
}
