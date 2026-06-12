import { useEffect, useRef, useState, type KeyboardEvent } from "react";

interface Props {
  sending: boolean;
  onSend: (text: string) => void;
  onCancel?: () => void;
  lastToolName?: string;
  toolCount?: number;
}

/** Calm brutalist input — single hairline border, accent on focus only.
 *  No live strip, no nested borders. Just `user@homunculus:~$ <text>`
 *  with a small bracketed action on the right.
 */
export function BrutalistChatInput({ sending, onSend, onCancel }: Props) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!sending && ref.current) ref.current.focus();
  }, [sending]);

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
  const borderColor = sending
    ? "var(--color-amber)"
    : focused
      ? "var(--color-border-bright)"
      : "var(--color-border)";

  return (
    <div
      className="brut-chat-input-shell fixed bottom-0 right-0 z-20 pt-3 pb-4"
      style={{
        background:
          "linear-gradient(to top, var(--color-bg) 60%, rgba(5, 5, 5, 0))",
        fontFamily: "var(--font-mono)",
      }}
    >
      <style>{`
        .brut-chat-input-shell { left: 220px; }
        @media (max-width: 760px) {
          .brut-chat-input-shell {
            left: 0;
          }
          .brut-chat-input-inner {
            padding-left: 16px;
            padding-right: 16px;
          }
          .brut-chat-input-prompt {
            display: none;
          }
        }
        .brut-input:focus,
        .brut-input:focus-visible { outline: none !important; box-shadow: none !important; }
      `}</style>
      {/* 720px — MUST match BrutalistChatLog's column in ChatPage. At
          860px the input rendered wider than the conversation and the
          whole page read as drifted right. */}
      <div className="brut-chat-input-inner max-w-[720px] mx-auto px-10">
        <div
          className="flex items-start gap-2"
          style={{
            background: "var(--color-bg)",
            borderTop: `1px solid ${borderColor}`,
            paddingTop: 12,
            paddingBottom: 4,
          }}
        >
          <span
            className="brut-chat-input-prompt select-none"
            style={{
              color: "var(--color-accent)",
              fontSize: 13,
              lineHeight: "20px",
              flexShrink: 0,
            }}
          >
            user@homunculus:~$
          </span>
          <textarea
            ref={ref}
            value={value}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onChange={(e) => {
              setValue(e.target.value);
              const el = e.target as HTMLTextAreaElement;
              el.style.height = "auto";
              el.style.height = Math.max(44, Math.min(el.scrollHeight, 220)) + "px";
            }}
            onKeyDown={onKeyDown}
            placeholder={sending ? "agent working — press stop to interrupt" : ""}
            rows={2}
            disabled={sending}
            className="brut-input flex-1 block w-full bg-transparent resize-none disabled:cursor-not-allowed"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              lineHeight: "20px",
              color: "var(--color-text)",
              caretColor: "var(--color-accent)",
              border: "none",
              outline: "none",
              minHeight: "44px",
              boxShadow: "none",
            }}
          />
          {sending ? (
            <button
              onClick={onCancel}
              className="text-[11px] uppercase tracking-[0.14em] transition-colors pt-1"
              style={{ color: "var(--color-danger)", background: "transparent", border: "none" }}
            >
              [stop]
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!canSend}
              className="text-[11px] uppercase tracking-[0.14em] transition-colors pt-1 disabled:opacity-30"
              style={{ color: canSend ? "var(--color-accent)" : "var(--color-text-faint)", background: "transparent", border: "none" }}
            >
              [send ↵]
            </button>
          )}
        </div>
        <div
          className="mt-2 text-[10px] uppercase tracking-[0.14em] flex justify-between"
          style={{ color: "var(--color-text-faint)" }}
        >
          <span>↵ send · ⇧↵ newline · esc clear</span>
          <span style={{ color: sending ? "var(--color-amber)" : "var(--color-text-faint)" }}>
            ● {sending ? "working" : "idle"}
          </span>
        </div>
      </div>
    </div>
  );
}
