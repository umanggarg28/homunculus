import { useEffect, useRef, useState, type KeyboardEvent } from "react";

interface Props {
  sending: boolean;
  onSend: (text: string) => void;
  onCancel?: () => void;
  lastToolName?: string;
  toolCount?: number;
}

/** Palette entries. Two honest kinds: `/use` is the only real harness
 *  command the web chat parses (web_api dispatches it before the
 *  agent); everything else is a quickstart that inserts a
 *  natural-language prompt the agent's own tools handle. The hint
 *  text says which is which — no pretend commands.
 */
const PALETTE: { cmd: string; hint: string; insert: string }[] = [
  { cmd: "/use",      hint: "swap chat model live · bare /use lists models · /use reset", insert: "/use " },
  { cmd: "/remember", hint: "prompt → save something to memory",                          insert: "remember this: " },
  { cmd: "/task",     hint: "prompt → create a scheduled task",                           insert: "create a task: " },
  { cmd: "/recall",   hint: "prompt → search memory",                                     insert: "what do you remember about " },
  { cmd: "/skills",   hint: "prompt → list current skills",                               insert: "list your skills and what each one does" },
];

/** Calm brutalist input — single hairline border, accent on focus only.
 *  No live strip, no nested borders. Just `user@homunculus:~$ <text>`
 *  with a small bracketed action on the right.
 */
export function BrutalistChatInput({ sending, onSend, onCancel }: Props) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [paletteSel, setPaletteSel] = useState(0);
  const [paletteDismissed, setPaletteDismissed] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!sending && ref.current) ref.current.focus();
  }, [sending]);

  // Palette opens while the draft is still a bare command word — once a
  // space lands (e.g. after the `/use ` insert) the user is writing
  // arguments and the palette gets out of the way.
  const matches =
    value.startsWith("/") && !/[\s]/.test(value) && !paletteDismissed
      ? PALETTE.filter((p) => p.cmd.startsWith(value))
      : [];
  const paletteOpen = matches.length > 0 && !sending;
  const sel = Math.min(paletteSel, Math.max(matches.length - 1, 0));

  const applyPaletteEntry = (entry: { insert: string }) => {
    setValue(entry.insert);
    setPaletteSel(0);
    ref.current?.focus();
  };

  const submit = () => {
    const text = value.trim();
    if (!text || sending) return;
    onSend(text);
    setValue("");
    setPaletteDismissed(false);
    if (ref.current) ref.current.style.height = "auto";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (paletteOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); setPaletteSel((s) => (s + 1) % matches.length); return; }
      if (e.key === "ArrowUp")   { e.preventDefault(); setPaletteSel((s) => (s - 1 + matches.length) % matches.length); return; }
      if (e.key === "Tab" || e.key === "Enter") { e.preventDefault(); applyPaletteEntry(matches[sel]); return; }
      if (e.key === "Escape") { e.preventDefault(); setPaletteDismissed(true); return; }
    }
    if (e.key === "Escape" && value) {
      // The footer has promised "esc clear" since day one — honor it.
      e.preventDefault();
      setValue("");
      if (ref.current) ref.current.style.height = "auto";
      return;
    }
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
      <div className="brut-chat-input-inner max-w-[720px] mx-auto px-10" style={{ position: "relative" }}>
        {paletteOpen && (
          <div
            style={{
              position: "absolute",
              bottom: "100%",
              left: 40,
              right: 40,
              marginBottom: 4,
              background: "var(--color-bg)",
              border: "1px solid var(--color-border-strong)",
              fontFamily: "var(--font-mono)",
              zIndex: 30,
            }}
          >
            {matches.map((p, i) => (
              <div
                key={p.cmd}
                onMouseDown={(e) => { e.preventDefault(); applyPaletteEntry(p); }}
                onMouseEnter={() => setPaletteSel(i)}
                className="px-3 py-2 flex gap-4 items-baseline"
                style={{
                  cursor: "pointer",
                  background: i === sel ? "color-mix(in srgb, var(--color-accent) 10%, transparent)" : "transparent",
                  borderLeft: `2px solid ${i === sel ? "var(--color-accent)" : "transparent"}`,
                }}
              >
                <span
                  className="text-[12px]"
                  style={{ color: i === sel ? "var(--color-accent)" : "var(--color-text)", width: 90, flexShrink: 0 }}
                >
                  {p.cmd}
                </span>
                <span className="text-[10px] uppercase tracking-[0.1em] truncate" style={{ color: "var(--color-text-muted)" }}>
                  {p.hint}
                </span>
              </div>
            ))}
            <div
              className="px-3 py-1.5 text-[9px] uppercase tracking-[0.16em]"
              style={{ color: "var(--color-text-faint)", borderTop: "1px solid var(--color-border)" }}
            >
              ↑↓ select · tab/↵ insert · esc dismiss
            </div>
          </div>
        )}
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
              setPaletteDismissed(false);
              setPaletteSel(0);
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
          <span>↵ send · ⇧↵ newline · esc clear · / commands</span>
          <span style={{ color: sending ? "var(--color-amber)" : "var(--color-text-faint)" }}>
            ● {sending ? "working" : "idle"}
          </span>
        </div>
      </div>
    </div>
  );
}
