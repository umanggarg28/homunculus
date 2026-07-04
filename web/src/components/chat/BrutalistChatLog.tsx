import { Fragment, useLayoutEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import type { ChatMessage } from "@/hooks/useChatStream";
import type { ToolCallEntry } from "./ToolCallCard";
import { BrutalistMessage, TransmissionRow } from "./BrutalistMessage";
import { BrutalistLanding } from "./BrutalistLanding";
import { useAutoScroll } from "@/hooks/useAutoScroll";

interface Props {
  messages: ChatMessage[];
  /** True when a view filter hides part of the history — an empty
   *  filtered log must not fall back to the first-run landing. */
  filterActive?: boolean;
  toolTimeline: ToolCallEntry[];
  sending: boolean;
  bootDone: boolean;
  historyLoading: boolean;
  onPickPrompt: (text: string) => void;
}

/** Months of history render slow and scroll worse — the log opens on
 *  the most recent tail and pages backward on demand. */
const WINDOW_INITIAL = 60;
const WINDOW_STEP = 100;

/** The brutalist chat surface: a single column of prompt rows
 *  and reasoning lines, with KEY|VAL|STATUS tool blocks between
 *  user turn and final agent reply. */
export function BrutalistChatLog({ messages, filterActive, toolTimeline, sending, bootDone, historyLoading, onPickPrompt }: Props) {
  const [windowSize, setWindowSize] = useState(WINDOW_INITIAL);
  // Expanding the window adds content ABOVE the viewport; compensate
  // the scroll position so the reader stays on the message they were
  // looking at instead of visually teleporting.
  const anchorHeightRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (anchorHeightRef.current !== null) {
      window.scrollBy(0, document.documentElement.scrollHeight - anchorHeightRef.current);
      anchorHeightRef.current = null;
    }
  }, [windowSize]);
  const showEarlier = () => {
    anchorHeightRef.current = document.documentElement.scrollHeight;
    setWindowSize((s) => s + WINDOW_STEP);
  };
  // Tool calls belong to the most recent assistant message — for
  // earlier turns we only have the message text. (Same approach the
  // old ChatLog took.)
  const assistantToolCalls = useMemo(() => {
    const map = new Map<string, ToolCallEntry[]>();
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant) map.set(lastAssistant.id, toolTimeline);
    return map;
  }, [messages, toolTimeline]);

  // Trigger auto-scroll on the streaming content's growing length. Using
  // the last message's character count means we scroll on every chunk
  // during streaming, not just message-count changes.
  const scrollTrigger = useMemo(() => {
    const last = messages[messages.length - 1];
    const lastLen = (last?.content ?? "").length;
    return `${messages.length}:${lastLen}:${toolTimeline.length}:${sending ? 1 : 0}`;
  }, [messages, toolTimeline, sending]);
  useAutoScroll(scrollTrigger);

  if (historyLoading) {
    return <SessionLoader />;
  }

  if (messages.length === 0) {
    if (filterActive) {
      return (
        <div
          className="text-[10px] uppercase tracking-[0.18em] py-10 text-center"
          style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-faint)" }}
        >
          ── no messages in this view ──
        </div>
      );
    }
    return <BrutalistLanding bootDone={bootDone} onPick={onPickPrompt} />;
  }

  const windowed = messages.slice(-windowSize);
  const hiddenCount = messages.length - windowed.length;

  // Turn numbering = number of user messages so far + 1 for the
  // current assistant reply that belongs to the same turn.
  //
  // Day dividers: timestamps render as bare HH:MM, so without a marker a
  // session spanning midnight reads as one out-of-order thread (04:46
  // above yesterday's 04:41). A divider is emitted whenever the calendar
  // day changes — and above the first message when it isn't from today,
  // so the oldest group is never the only unlabeled one.
  let turn = 0;
  let prevDay: string | null = null;
  return (
    <div className="flex flex-col gap-3">
      {hiddenCount > 0 && (
        <button
          onClick={showEarlier}
          className="mx-auto text-[10px] uppercase tracking-[0.18em] py-2 px-4"
          style={{
            fontFamily: "var(--font-mono)",
            background: "transparent",
            border: "1px solid var(--color-border)",
            color: "var(--color-text-muted)",
            cursor: "pointer",
          }}
        >
          ↑ show earlier · {hiddenCount} hidden
        </button>
      )}
      {windowed.map((m) => {
        if (m.role === "user") turn += 1;
        const tools = (m.role === "assistant" && assistantToolCalls.get(m.id)) || [];
        const day = m.ts ? dayKey(new Date(m.ts)) : prevDay;
        const needsDivider =
          day !== null && day !== prevDay && (prevDay !== null || day !== dayKey(new Date()));
        if (day !== null) prevDay = day;
        return (
          <Fragment key={m.id}>
            {needsDivider && m.ts && <DayDivider ts={m.ts} />}
            {m.kind === "transmission" ? (
              <TransmissionRow message={m} />
            ) : (
              <BrutalistMessage message={m} toolCalls={tools} sending={sending} turnNumber={turn} />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function DayDivider({ ts }: { ts: string }) {
  const d = new Date(ts);
  const now = new Date();
  const dateStr = d
    .toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    .replace(",", " ·");
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const label =
    dayKey(d) === dayKey(now)
      ? `today · ${dateStr}`
      : dayKey(d) === dayKey(yesterday)
        ? `yesterday · ${dateStr}`
        : dateStr;

  return (
    <div
      role="separator"
      aria-label={dateStr}
      className="flex items-center gap-3 my-2 select-none"
      style={{ fontFamily: "var(--font-mono)" }}
    >
      <div style={{ flex: 1, borderTop: "1px solid var(--color-border)" }} />
      <span
        className="text-[9px] uppercase tracking-[0.22em] whitespace-nowrap"
        style={{ color: "var(--color-text-faint)" }}
      >
        {label}
      </span>
      <div style={{ flex: 1, borderTop: "1px solid var(--color-border)" }} />
    </div>
  );
}

function SessionLoader() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: 220,
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.8,
          color: "var(--color-text-dim)",
          padding: "18px 24px",
          border: "1px solid var(--color-border)",
          background: "rgba(0,0,0,0.35)",
          minWidth: 280,
        }}
      >
        <div style={{ color: "var(--color-accent)" }}>$ homunculus --restore-session</div>
        <div>
          <span style={{ color: "var(--color-accent)" }}>[OK]</span>
          <span> loading session history</span>
        </div>
        <div style={{ marginTop: 4 }}>
          <span style={{ color: "var(--color-text-muted)" }}>&gt; </span>
          <motion.span
            animate={{ opacity: [1, 0] }}
            transition={{ duration: 0.55, repeat: Infinity, ease: "linear" }}
            style={{
              display: "inline-block",
              width: 8,
              height: 13,
              background: "var(--color-accent)",
              verticalAlign: "middle",
            }}
          />
        </div>
      </div>
    </motion.div>
  );
}
