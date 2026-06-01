import { useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/Button";

interface Props {
  sending: boolean;
  onSend: (text: string) => void;
  onCancel?: () => void;
}

export function ChatInput({ sending, onSend, onCancel }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const text = value.trim();
    if (!text || sending) return;
    onSend(text);
    setValue("");
    if (ref.current) ref.current.style.height = "auto";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const canSend = !!value.trim() && !sending;

  return (
    <div
      className="chat-input-shell fixed bottom-0 right-0 z-20 pt-6 pb-5"
      style={{
        background:
          "linear-gradient(to top, var(--color-bg) 70%, rgba(8, 9, 10, 0.0))",
      }}
    >
      <style>{`
        .chat-input-shell { left: 220px; }
        @media (max-width: 760px) {
          .chat-input-shell { left: 0; }
          .chat-input-inner {
            padding-left: 16px;
            padding-right: 16px;
          }
        }
      `}</style>
      <div className="chat-input-inner max-w-[760px] mx-auto px-8">
        <div
          className="relative rounded-[8px]"
          style={{
            background: "var(--color-surface-2)",
            border: "1px solid var(--color-border-strong)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
          }}
        >
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              const el = e.target as HTMLTextAreaElement;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 220) + "px";
            }}
            onKeyDown={onKeyDown}
            placeholder={sending ? "Agent is working…" : "Type a message…"}
            rows={1}
            disabled={sending}
            className="block w-full bg-transparent text-[var(--color-text)]
                       placeholder-[var(--color-text-faint)] resize-none outline-none
                       px-4 pt-3 pb-12 disabled:cursor-not-allowed"
            style={{ fontFamily: "var(--font-sans)", fontSize: "14px", lineHeight: 1.5 }}
          />
          <div className="absolute bottom-2.5 right-2.5 flex items-center gap-2">
            <span className="text-[11px] text-[var(--color-text-faint)]">
              {sending ? "press stop to interrupt" : "↵ send · ⇧↵ newline"}
            </span>
            {sending ? (
              <Button size="sm" variant="danger" onClick={onCancel} disabled={!onCancel}>
                Stop
              </Button>
            ) : (
              <Button size="sm" variant="primary" onClick={submit} disabled={!canSend}>
                Send
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
