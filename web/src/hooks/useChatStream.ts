import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { decodeJsonSseData, parseSseChunks } from "@/lib/sse";
import { useEventStream } from "@/hooks/useEventStream";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  inFlight?: boolean;
  source?: "web" | "telegram" | "repl" | "heartbeat" | string;
  /** ISO timestamp when this message was created. Live messages
   *  stamp it at send; persisted messages pick it up from the
   *  session record if available, omit otherwise. The UI must NOT
   *  fabricate a timestamp from Date.now() — that lies about when
   *  the message actually happened. */
  ts?: string;
}

interface UseChatStream {
  messages: ChatMessage[];
  sending: boolean;
  historyLoading: boolean;
  send: (text: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

/** Owns chat-log state and orchestrates the streaming POST to
 * /api/chat/send. The streaming text accumulates onto the last
 * assistant message in place, so React re-renders are diff-cheap.
 * Exposes cancel() to interrupt the in-flight reply. */
export function useChatStream(): UseChatStream {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  // Track the current request so cancel() can both notify the server
  // and abort the local fetch read loop.
  const activeStreamIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.chatHistory()
      .then((history) => { if (!cancelled) setMessages(history); })
      .catch(() => undefined)
      .finally(() => { if (!cancelled) setHistoryLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // Cross-transport sync: when a persisted turn lands in the events
  // stream and the local /api/chat/send isn't actively writing, pull
  // the chat log so the transcript reflects messages from any source —
  // telegram, heartbeat-driven notifications, OR a parallel browser
  // tab / curl call hitting /api/chat/send. The `sending` guard is
  // what prevents double-rendering during the local stream; once the
  // local send completes, this effect picks up any concurrent changes
  // that landed in the meantime.
  const { events: feedEvents } = useEventStream(40);
  const lastSyncedEventTsRef = useRef<string | null>(null);
  useEffect(() => {
    if (sending) return;
    const triggers = feedEvents.filter(
      (e) => e.event === "user_message" || e.event === "assistant_reply",
    );
    if (triggers.length === 0) return;
    const newest = triggers[triggers.length - 1];
    if (!newest.ts || newest.ts === lastSyncedEventTsRef.current) return;
    lastSyncedEventTsRef.current = newest.ts;
    api.chatHistory()
      .then((history) => setMessages(history))
      .catch(() => undefined);
  }, [feedEvents, sending]);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || sending) return;
    setSending(true);

    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    const streamId = crypto.randomUUID();
    activeStreamIdRef.current = streamId;

    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", content: text, ts: new Date().toISOString() },
      { id: assistantId, role: "assistant", content: "", inFlight: true, ts: new Date().toISOString() },
    ]);

    try {
      const r = await api.chatSend(text, streamId);
      if (!r.ok) throw new Error(`chat failed: ${r.status} ${r.statusText}`);
      if (!r.body) throw new Error("chat response had no body");
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawDone = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remainder } = parseSseChunks(buffer);
        buffer = remainder;
        for (const evt of events) {
          if (evt.event === "done") { sawDone = true; continue; }
          appendToAssistant(setMessages, assistantId, decodeJsonSseData(evt.data));
        }
        if (sawDone) break;
      }
      if (buffer.trim()) {
        const { events } = parseSseChunks(buffer + "\n\n");
        for (const evt of events) {
          if (evt.event !== "done") {
            appendToAssistant(setMessages, assistantId, decodeJsonSseData(evt.data));
          }
        }
      }
    } catch (e) {
      appendToAssistant(setMessages, assistantId, `\n[error: ${(e as Error).message}]`);
    } finally {
      activeStreamIdRef.current = null;
      abortRef.current = null;
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, inFlight: false } : m)),
      );
      setSending(false);
    }
  }, [sending]);

  const cancel = useCallback(() => {
    const id = activeStreamIdRef.current;
    if (!id) return;
    // Tell the server to stop emitting; the stream's "done" event still
    // arrives and the fetch loop exits naturally with [stopped by user]
    // appended by the server.
    api.chatCancel(id).catch(() => undefined);
  }, []);

  const reset = useCallback(() => { setMessages([]); }, []);

  return { messages, sending, historyLoading, send, cancel, reset };
}

function appendToAssistant(
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  assistantId: string,
  chunk: string,
) {
  setMessages((prev) =>
    prev.map((m) =>
      m.id === assistantId ? { ...m, content: m.content + chunk } : m,
    ),
  );
}
