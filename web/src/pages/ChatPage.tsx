import { useEffect, useMemo, useState } from "react";
import { useChatStream } from "@/hooks/useChatStream";
import { useApprovals } from "@/hooks/useApprovals";
import { BrutalistChatLog } from "@/components/chat/BrutalistChatLog";
import { BrutalistChatInput } from "@/components/chat/BrutalistChatInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";

export function ChatPage() {
  const { messages, toolCalls, sending, historyLoading, send, cancel, reload } = useChatStream();
  // A decision on the approvals screen starts a turn outside this screen. When the
  // agent's reply to it lands, load the conversation again so it shows here.
  const { approvals } = useApprovals();
  const replies = approvals.filter((a) => a.follow_up !== null).length;
  // Runs only when the number of follow-ups changes: reloading after an ordinary
  // reply would replace the live turn's tool steps with the plainer history.
  useEffect(() => {
    if (replies > 0 && !sending) reload();
  }, [replies]);
  const [bootDone, setBootDone] = useState(false);
  // Ticks every 5s so "last reply 14m ago" stays current without
  // re-rendering the whole log every second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const t = setTimeout(() => setBootDone(true), 380);
    return () => clearTimeout(t);
  }, []);

  const userTurnCount = messages.filter((m) => m.role === "user" && !m.notice).length;
  const subtitle = `turn ${userTurnCount}` + (sending ? " · ● working" : "");

  // Shown while idle: how long ago the last completed reply arrived. Only replies
  // made in this browser carry a time, so the pane stays hidden for loaded history.
  const nowPane = useMemo(() => {
    if (sending) return null;
    const lastReply = [...messages].reverse().find((m) => m.role === "assistant" && m.ts && !m.error);
    if (!lastReply?.ts) return null;
    const ageSec = Math.max(0, Math.floor((nowMs - new Date(lastReply.ts).getTime()) / 1000));
    if (ageSec < 60) return { ago: `${ageSec}s` };
    if (ageSec < 3600) return { ago: `${Math.floor(ageSec / 60)}m` };
    if (ageSec < 86400) return { ago: `${Math.floor(ageSec / 3600)}h` };
    return { ago: `${Math.floor(ageSec / 86400)}d` };
  }, [sending, messages, nowMs]);

  return (
    <PageShell>
      <PageHeader title="Chat" subtitle={subtitle} />

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
            <div className="text-[9px] uppercase tracking-[0.28em]" style={{ color: "var(--color-text-faint)" }}>
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

      <div className="max-w-[720px] mx-auto" style={{ paddingBottom: 96 }}>
        <BrutalistChatLog
          messages={messages}
          toolCalls={toolCalls}
          sending={sending}
          bootDone={bootDone}
          historyLoading={historyLoading}
          onPickPrompt={send}
        />
      </div>

      <BrutalistChatInput sending={sending} onSend={send} onCancel={cancel} />
    </PageShell>
  );
}
