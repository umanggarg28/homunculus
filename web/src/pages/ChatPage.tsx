import { useState } from "react";
import { useChatStream } from "@/hooks/useChatStream";
import { ChatLog } from "@/components/chat/ChatLog";
import { ChatInput } from "@/components/chat/ChatInput";
import { Toast } from "@/components/ui/Toast";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";

export function ChatPage() {
  const { messages, sending, send, cancel, reset } = useChatStream();
  const [toast, setToast] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const closeChapter = async () => {
    if (closing || messages.length === 0) return;
    if (!confirm("Archive this conversation and start fresh?")) return;
    setClosing(true);
    try {
      await api.chapterClose();
      reset();
      setToast("Conversation archived. Starting fresh.");
    } catch {
      setToast("Couldn't close conversation. Try again.");
    } finally {
      setClosing(false);
    }
  };

  return (
    <>
      {messages.length > 0 && (
        <div
          className="sticky top-0 z-10 flex items-center justify-between px-8 h-12"
          style={{
            background: "rgba(8, 9, 10, 0.85)",
            backdropFilter: "blur(8px)",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <div className="text-[13px] font-medium text-[var(--color-text-dim)]">
            Current conversation · {messages.length} messages
          </div>
          <Button size="sm" variant="ghost" onClick={closeChapter} disabled={closing}>
            {closing ? "Archiving…" : "Close conversation"}
          </Button>
        </div>
      )}

      <div
        className="max-w-[760px] mx-auto px-8 pt-10"
        style={{ paddingBottom: 160 }}
      >
        <ChatLog messages={messages} onPickPrompt={send} />
      </div>
      <ChatInput sending={sending} onSend={send} onCancel={cancel} />
      <Toast message={toast} onDismiss={() => setToast(null)} />
    </>
  );
}
