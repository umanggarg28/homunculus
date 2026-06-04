import { useEffect, useMemo, useState } from "react";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatToolCalls } from "@/hooks/useChatToolCalls";
import { useEventStream } from "@/hooks/useEventStream";
import { BrutalistChatLog } from "@/components/chat/BrutalistChatLog";
import { BrutalistChatInput } from "@/components/chat/BrutalistChatInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { api } from "@/lib/api";

export function ChatPage() {
  const { messages, sending, historyLoading, send, cancel } = useChatStream();
  const toolTimeline = useChatToolCalls();
  // Subscribe to the SSE event stream to get real timestamps for the
  // last assistant_reply (message IDs are UUIDs and don't encode time).
  const { events } = useEventStream(160);
  const [sessionId] = useState(() => crypto.randomUUID().slice(0, 6));
  const [closing, setClosing] = useState(false);
  const [bootDone, setBootDone] = useState(false);
  // Tick every 5s so the NOW pane's "last reply 14m ago" stays fresh
  // without re-rendering the entire log every second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setBootDone(true), 380);
    return () => clearTimeout(t);
  }, []);

  const closeChapter = async () => {
    if (closing || messages.length === 0) return;
    if (!confirm("ARCHIVE THIS SESSION AND OPEN A NEW ONE?")) return;
    setClosing(true);
    try {
      await api.chapterClose();
    } catch {
      // archive failed — still open a fresh session
    } finally {
      window.location.reload();
    }
  };

  const userTurnCount = messages.filter((message) => message.role === "user").length;
  const subtitle = `session ${sessionId} · turn ${userTurnCount}` +
    (sending ? " · ● working" : "");

  // NOW pane state: live thinking is owned by the inline ThinkingIndicator
  // inside each turn — we only surface a top-level anchor when the chat
  // is IDLE (no active stream, has prior history). Otherwise return null.
  const nowPane = useMemo(() => {
    if (sending) return null;
    if (messages.length === 0) return null;
    // Find the most recent assistant_reply event in the SSE stream;
    // that's the authoritative source of the last reply time.
    const lastReply = [...events].reverse().find((e) => e.event === "assistant_reply" && e.service === "web");
    if (!lastReply) return null;
    const lastMs = new Date(lastReply.ts).getTime();
    const ageSec = Math.max(0, Math.floor((nowMs - lastMs) / 1000));
    let ago: string;
    if (ageSec < 60)      ago = `${ageSec}s`;
    else if (ageSec < 3600) ago = `${Math.floor(ageSec / 60)}m`;
    else if (ageSec < 86400) ago = `${Math.floor(ageSec / 3600)}h`;
    else                    ago = `${Math.floor(ageSec / 86400)}d`;
    return { ago };
  }, [sending, messages.length, events, nowMs]);

  return (
    <PageShell>
      <PageHeader
          title="Chat"
          subtitle={subtitle}
          actions={
            messages.length > 0 ? (
              <button
                onClick={closeChapter}
                disabled={closing}
                className="hm-text-command text-[10px] uppercase tracking-[0.16em] disabled:opacity-50"
                style={{
                  padding: 0,
                  cursor: closing ? "default" : "pointer",
                }}
              >
                [{closing ? "archiving…" : "close session"}]
              </button>
            ) : undefined
          }
      />

      {/* NOW pane — single bold anchor for Chat. Hidden when sending
          (the inline ThinkingIndicator owns the screen during active
          turns) and hidden on empty chat (BrutalistLanding fills the
          space instead). Shows last-reply age when idle so a returning
          user immediately sees how stale the conversation is. */}
      {nowPane && (
        <div
          className="max-w-[720px] mx-auto mb-8"
          style={{
            fontFamily: "var(--font-mono)",
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 16,
            borderBottom: "1px solid var(--color-border)",
            paddingBottom: 12,
          }}
        >
          <div>
            <div
              className="text-[9px] uppercase tracking-[0.28em]"
              style={{ color: "var(--color-text-faint)" }}
            >
              now
            </div>
            <div
              className="text-[26px] mt-1"
              style={{ color: "var(--color-text-dim)", letterSpacing: "0", lineHeight: 1.1 }}
            >
              idle
            </div>
          </div>
          <div
            className="text-[10px] uppercase tracking-[0.18em] text-right"
            style={{ color: "var(--color-text-faint)" }}
          >
            <div>last reply</div>
            <div className="mt-1" style={{ color: "var(--color-text-muted)" }}>
              {nowPane.ago} ago
            </div>
          </div>
        </div>
      )}

      {/* Reduced max-width from 860 → 720 for better line-length
          (CSS body comfortably reads at 60–80 chars; mono ~ 80 chars
          at 720px). Helps the editorial feel without changing tone. */}
      <div className="max-w-[720px] mx-auto" style={{ paddingBottom: 120 }}>
        <BrutalistChatLog
          messages={messages}
          toolTimeline={toolTimeline}
          sending={sending}
          bootDone={bootDone}
          historyLoading={historyLoading}
          onPickPrompt={send}
        />
      </div>

      <BrutalistChatInput
        sending={sending}
        onSend={send}
        onCancel={cancel}
      />
    </PageShell>
  );
}
