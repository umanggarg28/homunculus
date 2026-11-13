import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { MarkdownMessage } from "./Markdown";
import { useApprovals } from "@/hooks/useApprovals";
import { useChatStream, type ChatMessage } from "@/hooks/useChatStream";
import type { ToolStep } from "@/lib/toolSteps";
import { bounce, drawBus, send as sendTrace, type Tone } from "@/presence/overlay";
import { Composer } from "./Composer";
import { LiveView } from "./LiveView";
import { WorkCard } from "./WorkCard";

const hhmm = (iso?: string) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : null;

// If you scrolled the thread in the last few seconds, new work does not pull you back
// mid-read; a chip offers to take you there instead.
const READING_MS = 5000;
const TONE: Record<ToolStep["outcome"], Tone> = { ok: "live", failed: "bad", waiting: "attn" };

function Message({ m, children }: { m: ChatMessage; children?: React.ReactNode }) {
  const time = hhmm(m.ts);
  if (m.role === "user" && m.notice) {
    return <p className="msg-note">{m.content}</p>;
  }
  if (m.role === "user") {
    return (
      <div className="msg-you">
        {m.content}
        {time && <span className="time">{time}</span>}
      </div>
    );
  }
  const waiting = m.inFlight && !m.content;
  return (
    <div className="msg-it">
      {waiting ? (
        <p className="msg-wait" aria-label="Thinking">
          <i />
          <i />
          <i />
        </p>
      ) : (
        m.content && <MarkdownMessage text={m.content} />
      )}
      {children}
      {m.error && <p className={m.error === "stopped" ? "msg-stopped" : "msg-error"}>{m.error === "stopped" ? "Stopped by you. Steps that already ran were not undone." : m.error}</p>}
      {time && !m.inFlight && <span className="time">{time}</span>}
    </div>
  );
}

/** The conversation: your messages, the agent's replies, and under the reply being
 *  written, a card of the steps it took. The thread follows the work as it arrives
 *  unless you are reading further up. */
export function Thread({ onReview, ask }: { onReview: () => void; ask: string | null }) {
  const { messages, toolCalls, sending, historyLoading, send, cancel, reload } = useChatStream();
  const { approvals } = useApprovals();
  const thread = useRef<HTMLDivElement>(null);
  const readingAt = useRef(-Infinity);
  const [chip, setChip] = useState(false);
  const [hud, setHud] = useState<{ step: number | null } | null>(null);
  // Steps already drawn, so only a new one arrives with the trace.
  const [seen, setSeen] = useState(0);

  // A decision on an approval starts a turn this screen does not watch. When the agent's
  // reply to it lands, load the conversation again so it shows here.
  const replies = approvals.filter((a) => a.follow_up !== null).length;
  useEffect(() => {
    if (replies > 0 && !sending) reload();
    // Only when the number of follow-ups changes: reloading after an ordinary reply
    // would replace the live turn's steps with the plainer history.
  }, [replies]);

  // The newest step: the presence sends a trace to its row, and the row comes to full
  // strength as the trace lands. A refused step is a trace that stops at a wall.
  useEffect(() => {
    if (toolCalls.length <= seen) {
      if (toolCalls.length < seen) setSeen(toolCalls.length);
      return;
    }
    const step = toolCalls[toolCalls.length - 1];
    const row = thread.current?.querySelector(`[data-step="${toolCalls.length - 1}"]`) ?? null;
    if (step.outcome === "failed" && step.answer !== "act") bounce("bad");
    sendTrace(row, TONE[step.outcome]);
    const t = setTimeout(() => setSeen(toolCalls.length), 1400);
    return () => clearTimeout(t);
  }, [toolCalls, seen]);

  // Follow the action.
  const latest = `${messages.length}:${messages[messages.length - 1]?.content.length ?? 0}:${toolCalls.length}`;
  useLayoutEffect(() => {
    const el = thread.current;
    if (!el) return;
    if (performance.now() - readingAt.current < READING_MS) {
      const below = el.scrollHeight - el.scrollTop - el.clientHeight > 48;
      setChip(below && sending);
      return;
    }
    el.scrollTop = el.scrollHeight;
    setChip(false);
  }, [latest, sending]);

  const reading = () => {
    readingAt.current = performance.now();
  };
  const backToLive = () => {
    readingAt.current = -Infinity;
    const el = thread.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    setChip(false);
  };

  useEffect(() => {
    if (!hud) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setHud(null);
    document.addEventListener("keydown", close);
    // The sheet covers the thread's lane of the bus, so the bus is drawn without it.
    document.documentElement.dataset.hud = "1";
    drawBus();
    return () => {
      document.removeEventListener("keydown", close);
      delete document.documentElement.dataset.hud;
      drawBus();
    };
  }, [hud]);

  const lastReply = [...messages].reverse().find((m) => m.role === "assistant");
  const cardOn = toolCalls.length > 0 && lastReply && (sending || lastReply.ts) ? lastReply.id : null;
  const empty = !historyLoading && messages.length === 0;

  return (
    <>
      <div
        className="thread"
        ref={thread}
        onWheel={reading}
        onTouchMove={reading}
        onKeyDown={(e) => ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(e.key) && reading()}
      >
        {empty && (
          <div className="msg-it brief">
            <p>Ask for something to be done. It asks you before anything risky.</p>
            <div className="choices">
              {["List what is in my workspace", "Save a note called ideas.md"].map((s) => (
                <button key={s} type="button" className="btn" onClick={() => send(s)}>
                  <span>{s}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <Message key={m.id} m={m}>
            {m.id === cardOn && (
              <WorkCard steps={toolCalls} running={sending} seen={seen} open={hud?.step ?? null} onOpen={(step) => setHud({ step })} />
            )}
          </Message>
        ))}
      </div>
      {ask && (
        <button type="button" className="ask-dock" onClick={onReview}>
          <span className="dock-title">{ask}</span>
          <span className="dock-cta">Review</span>
        </button>
      )}
      {chip && (
        <button type="button" className="live-chip" onClick={backToLive}>
          <span className="live-dot" aria-hidden="true" />
          {toolCalls.length ? `Live, step ${toolCalls.length}` : "Live"}
        </button>
      )}
      <Composer sending={sending} onSend={send} onStop={cancel} />
      {hud && toolCalls.length > 0 && (
        <LiveView steps={toolCalls} running={sending} step={hud.step} onStep={(step) => setHud({ step })} onClose={() => setHud(null)} />
      )}
    </>
  );
}
