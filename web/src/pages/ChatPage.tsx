import { useEffect, useMemo, useState } from "react";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatToolCalls } from "@/hooks/useChatToolCalls";
import { useEventStream } from "@/hooks/useEventStream";
import { BrutalistChatLog } from "@/components/chat/BrutalistChatLog";
import { BrutalistChatInput } from "@/components/chat/BrutalistChatInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { Tooltip } from "@/components/ui/Tooltip";
import { useArmedAction } from "@/hooks/useArmedAction";
import { api } from "@/lib/api";

type ChatView = "all" | "chat" | "tx";

export function ChatPage() {
  const { messages, sending, historyLoading, send, cancel } = useChatStream();
  const toolTimeline = useChatToolCalls();
  // Transmissions can outnumber chat turns 2:1 — the view filter lets
  // the log read as pure conversation (or pure delivery ledger) on
  // demand. Persisted: a noise preference is not a per-visit decision.
  const [view, setView] = useState<ChatView>(() => {
    const saved = localStorage.getItem("hm-chat-view");
    return saved === "chat" || saved === "tx" ? saved : "all";
  });
  const pickView = (v: ChatView) => {
    setView(v);
    localStorage.setItem("hm-chat-view", v);
  };
  const visibleMessages = useMemo(() => {
    if (view === "all") return messages;
    return messages.filter((m) =>
      view === "tx" ? m.kind === "transmission" : m.kind !== "transmission",
    );
  }, [messages, view]);
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

  // Arm/confirm instead of a browser confirm() — the OS dialog broke
  // the fiction and is easier to click through than a control that
  // visibly changes state and asks again.
  const { armed, arm, disarm } = useArmedAction();
  const closeChapter = async () => {
    if (closing || messages.length === 0) return;
    if (armed !== "close") { arm("close"); return; }
    disarm();
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
              <div className="flex items-baseline gap-5">
                <ViewToggle view={view} onPick={pickView} />
                <button
                  onClick={closeChapter}
                  disabled={closing}
                  className="hm-text-command text-[10px] uppercase tracking-[0.16em] disabled:opacity-50"
                  style={{
                    padding: 0,
                    cursor: closing ? "default" : "pointer",
                    color: armed === "close" ? "var(--color-danger)" : undefined,
                  }}
                >
                  [{closing ? "archiving…" : armed === "close" ? "confirm archive" : "close session"}]
                </button>
              </div>
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
      <div className="max-w-[720px] mx-auto" style={{ paddingBottom: 96 }}>
        <BrutalistChatLog
          messages={visibleMessages}
          filterActive={view !== "all"}
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

function ViewToggle({ view, onPick }: { view: ChatView; onPick: (v: ChatView) => void }) {
  const OPTIONS: { id: ChatView; label: string; hint: string }[] = [
    { id: "all",  label: "all",  hint: "Conversation and agent transmissions, interleaved." },
    { id: "chat", label: "chat", hint: "Only turns you initiated and their replies." },
    { id: "tx",   label: "tx",   hint: "Only unprompted agent deliveries (the notification ledger)." },
  ];
  return (
    <div
      className="flex items-baseline gap-1 text-[10px] uppercase tracking-[0.14em] select-none"
      style={{ fontFamily: "var(--font-mono)" }}
    >
      <span style={{ color: "var(--color-text-faint)", marginRight: 4 }}>view</span>
      {OPTIONS.map((o) => (
        <Tooltip key={o.id} text={o.hint} placement="bottom">
        <button
          onClick={() => onPick(o.id)}
          className="uppercase tracking-[0.14em]"
          style={{
            background: "transparent",
            border: "none",
            padding: "0 2px",
            cursor: "pointer",
            color: view === o.id ? "var(--color-accent)" : "var(--color-text-faint)",
            textShadow: view === o.id ? "0 0 8px var(--color-accent-glow)" : "none",
          }}
        >
          [{o.label}]
        </button>
        </Tooltip>
      ))}
    </div>
  );
}
