import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { parseSseChunks } from "@/lib/sse";
import { recordTurnEvent } from "@/lib/turnEvents";
import type { ToolStep } from "@/lib/toolSteps";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  inFlight?: boolean;
  /** ISO time the message was created in this browser. Messages loaded from
   *  history have none, because the server does not record one yet. */
  ts?: string;
  /** Set when the reply ended with an error instead of finishing. */
  error?: string;
}

interface UseChatStream {
  messages: ChatMessage[];
  /** Tool steps of the reply currently being written, in order. */
  toolCalls: ToolStep[];
  sending: boolean;
  historyLoading: boolean;
  send: (text: string) => Promise<void>;
  cancel: () => void;
}

/** A `tool` event as the server sends it. */
interface ToolEvent {
  name: string;
  arguments: string;
  answer: string;
  result: string;
  duration_ms: number;
}

/** Owns the chat log and the streaming POST to /api/chat. Text accumulates onto
 *  the last assistant message in place, so re-renders stay cheap. */
export function useChatStream(): UseChatStream {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolStep[]>([]);
  const [sending, setSending] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .history()
      .then((history) => {
        if (cancelled) return;
        setMessages(history.map((m) => ({ id: crypto.randomUUID(), ...m })));
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || sending) return;
      setSending(true);
      setToolCalls([]);
      const assistantId = crypto.randomUUID();
      const now = new Date().toISOString();
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: text, ts: now },
        { id: assistantId, role: "assistant", content: "", inFlight: true, ts: now },
      ]);
      recordTurnEvent({ event: "user_message", text });

      const update = (change: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? change(m) : m)));
      const fail = (message: string) => {
        update((m) => ({ ...m, error: message }));
        recordTurnEvent({ event: "error", text: message });
      };

      const abort = new AbortController();
      abortRef.current = abort;
      try {
        const response = await api.chat(text, abort.signal);
        if (!response.ok || !response.body) throw new Error(`request failed (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;
        while (!finished) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { events, remainder } = parseSseChunks(buffer);
          buffer = remainder;
          for (const event of events) {
            const data = JSON.parse(event.data || "{}");
            if (event.event === "delta") {
              update((m) => ({ ...m, content: m.content + data.text }));
            } else if (event.event === "tool") {
              const step = data as ToolEvent;
              setToolCalls((prev) => [
                ...prev,
                {
                  name: step.name,
                  answer: step.answer,
                  arguments: step.arguments,
                  result: step.result,
                  durationMs: step.duration_ms,
                },
              ]);
              recordTurnEvent({ event: "tool", name: step.name, answer: step.answer, result: step.result });
            } else if (event.event === "error") {
              fail(data.message);
              finished = true;
            } else if (event.event === "done") {
              recordTurnEvent({ event: "assistant_reply" });
              finished = true;
            }
          }
        }
      } catch (error) {
        if (!abort.signal.aborted) fail((error as Error).message);
        else {
          update((m) => ({ ...m, error: "stopped" }));
          recordTurnEvent({ event: "stopped" });
        }
      } finally {
        abortRef.current = null;
        update((m) => ({ ...m, inFlight: false }));
        setSending(false);
      }
    },
    [sending],
  );

  /** Stop reading the reply. The server sees the connection close and leaves the
   *  unfinished turn out of the conversation. Tool calls that already ran are not
   *  undone. */
  const cancel = useCallback(() => abortRef.current?.abort(), []);

  return { messages, toolCalls, sending, historyLoading, send, cancel };
}
