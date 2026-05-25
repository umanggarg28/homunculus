import { useEffect, useState } from "react";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatToolCalls } from "@/hooks/useChatToolCalls";
import { BrutalistChatLog } from "@/components/chat/BrutalistChatLog";
import { BrutalistChatInput } from "@/components/chat/BrutalistChatInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { api } from "@/lib/api";

export function ChatPage() {
  const { messages, sending, send, cancel, reset } = useChatStream();
  const toolTimeline = useChatToolCalls();
  const [sessionId] = useState(() => crypto.randomUUID().slice(0, 6));
  const [closing, setClosing] = useState(false);
  const [bootDone, setBootDone] = useState(false);

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
      reset();
    } catch {
      // surfaced via status bar otherwise
    } finally {
      setClosing(false);
    }
  };

  const subtitle = `session ${sessionId} · turn ${messages.length}` +
    (sending ? " · ● working" : "");

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
                className="text-[10px] uppercase tracking-[0.16em] transition-colors disabled:opacity-50"
                style={{
                  background: "transparent",
                  color: "var(--color-text-muted)",
                  border: "none",
                  padding: 0,
                  cursor: closing ? "default" : "pointer",
                }}
                onMouseEnter={(e) => {
                  if (!closing) (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
                }}
                onMouseLeave={(e) => {
                  if (!closing) (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-muted)";
                }}
              >
                [{closing ? "archiving…" : "close session"}]
              </button>
            ) : undefined
          }
      />

      <div className="max-w-[860px] mx-auto" style={{ paddingBottom: 180 }}>
        <BrutalistChatLog
          messages={messages}
          toolTimeline={toolTimeline}
          sending={sending}
          bootDone={bootDone}
          onPickPrompt={send}
        />
      </div>

      <BrutalistChatInput
        sending={sending}
        onSend={send}
        onCancel={cancel}
        lastToolName={toolTimeline.at(-1)?.name}
        toolCount={toolTimeline.length}
      />
    </PageShell>
  );
}
