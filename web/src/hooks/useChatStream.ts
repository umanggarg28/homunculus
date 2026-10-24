import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { parseSseChunks } from "@/lib/sse";
import { recordTurnEvent } from "@/lib/turnEvents";
import type { StepOutcome, ToolStep } from "@/lib/toolSteps";

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
  /** Written by the program on the operator's behalf, such as an approval outcome. */
  notice?: boolean;
}

interface UseChatStream {
  messages: ChatMessage[];
  /** Tool steps of the reply currently being written, in order. */
  toolCalls: ToolStep[];
  sending: boolean;
  historyLoading: boolean;
  send: (text: string) => Promise<void>;
  cancel: () => void;
  /** Load the conversation again, for turns that happened outside this screen. */
  reload: () => Promise<void>;
}

/** A `tool` event as the server sends it. */
interface ToolEvent {
  name: string;
  arguments: string;
  answer: string;
  result: string;
  duration_ms: number;
  outcome: StepOutcome;
}

// How a dropped connection to a turn is retried: after a short wait, a few times, the
// way a browser's EventSource retries a server-sent events stream.
const RECONNECT_DELAY_MS = 1000;
const RECONNECT_ATTEMPTS = 5;

/** Owns the chat log and the turn being watched. A turn runs on the server, so this
 *  hook only watches it: leaving the page stops the watching, [stop] stops the turn,
 *  and opening the page again rejoins a turn still in progress. Text accumulates onto
 *  the last assistant message in place, so re-renders stay cheap. */
export function useChatStream(): UseChatStream {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolStep[]>([]);
  const [sending, setSending] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const turnRef = useRef<string | null>(null);
  // [stop] pressed before the turn's first event arrived, so before its id was known.
  const stopWantedRef = useRef(false);

  /** Load the conversation from the server, replacing what is shown. */
  const reload = useCallback(async () => {
    try {
      const history = await api.history();
      setMessages(history.map((m) => ({ id: crypto.randomUUID(), ...m })));
    } catch {
      // Unreachable server: keep what is shown.
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  /** Watch a turn from its first event until it ends: one sent from here, when `text`
   *  is given, or one already running. If the connection drops first, it reconnects
   *  with the last event id seen, so nothing is missed or shown twice. Resolves true if
   *  the turn's end was seen. */
  const follow = useCallback(
    async (start: (signal: AbortSignal) => Promise<Response>, text?: string): Promise<boolean> => {
      setSending(true);
      setToolCalls([]);
      const assistantId = crypto.randomUUID();
      const now = new Date().toISOString();
      const placeholder = (message: string): ChatMessage[] => [
        { id: crypto.randomUUID(), role: "user", content: message, ts: now },
        { id: assistantId, role: "assistant", content: "", inFlight: true, ts: now },
      ];
      const update = (change: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? change(m) : m)));
      const fail = (message: string) => {
        update((m) => ({ ...m, error: message }));
        recordTurnEvent({ event: "error", text: message });
      };
      if (text !== undefined) {
        setMessages((prev) => [...prev, ...placeholder(text)]);
        recordTurnEvent({ event: "user_message", text });
      }
      const abort = new AbortController();
      abortRef.current = abort;
      let lastId = 0;
      let finished = false;
      let attempts = 0;
      try {
        let response = await start(abort.signal);
        while (!finished) {
          try {
            if (!response.ok || !response.body) throw new Error(`request failed (${response.status})`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            while (!finished) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const { events, remainder } = parseSseChunks(buffer);
              buffer = remainder;
              for (const event of events) {
                if (event.id !== undefined) lastId = Number(event.id);
                attempts = 0;
                const data = JSON.parse(event.data || "{}");
                if (event.event === "turn") {
                  turnRef.current = data.id;
                  if (text === undefined) setMessages((prev) => [...prev, ...placeholder(data.message)]);
                  if (stopWantedRef.current) api.stopTurn(data.id).catch(() => undefined);
                } else if (event.event === "delta") {
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
                      outcome: step.outcome,
                    },
                  ]);
                  recordTurnEvent({
                    event: "tool",
                    name: step.name,
                    answer: step.answer,
                    result: step.result,
                    outcome: step.outcome,
                  });
                } else if (event.event === "error") {
                  fail(data.message);
                  finished = true;
                } else if (event.event === "stopped") {
                  update((m) => ({ ...m, error: "stopped" }));
                  recordTurnEvent({ event: "stopped" });
                  finished = true;
                } else if (event.event === "done") {
                  recordTurnEvent({ event: "assistant_reply" });
                  finished = true;
                }
              }
            }
          } catch (error) {
            if (abort.signal.aborted) throw error;
            // A dropped connection: retried below, like a clean end before the last event.
          }
          if (finished) break;
          if (!turnRef.current || ++attempts > RECONNECT_ATTEMPTS) {
            throw new Error("lost the connection to the agent; reload to see where it got to");
          }
          await new Promise((resolve) => setTimeout(resolve, RECONNECT_DELAY_MS));
          response = await api.turnEvents(turnRef.current, lastId, abort.signal);
        }
      } catch (error) {
        // Leaving the page only stops the watching; the turn carries on on the server.
        if (!abort.signal.aborted) fail((error as Error).message);
      } finally {
        abortRef.current = null;
        turnRef.current = null;
        stopWantedRef.current = false;
        update((m) => ({ ...m, inFlight: false }));
        setSending(false);
      }
      return finished;
    },
    [],
  );

  useEffect(() => {
    // Show the conversation, then rejoin any turn still running, such as one started
    // before a reload, and then any turn queued behind it. `closed` guards against this
    // effect running twice, as React does in development.
    let closed = false;
    (async () => {
      await reload();
      while (!closed) {
        const running = await api.currentTurn().catch(() => null);
        if (!running || closed) break;
        const ended = await follow((signal) => api.turnEvents(running.id, 0, signal));
        if (!ended) break;
      }
    })();
    return () => {
      closed = true;
      abortRef.current?.abort();
    };
  }, [reload, follow]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || sending) return;
      await follow((signal) => api.chat(text, signal), text);
    },
    [sending, follow],
  );

  /** Stop the turn on the server. Its stream ends with a `stopped` event. Tool calls
   *  that already ran are not undone. */
  const cancel = useCallback(() => {
    if (turnRef.current) api.stopTurn(turnRef.current).catch(() => undefined);
    else stopWantedRef.current = true;
  }, []);

  return { messages, toolCalls, sending, historyLoading, send, cancel, reload };
}
