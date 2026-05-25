import { useEffect, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** Live event ticker. A single-line scrolling readout of incoming
 *  events — feels like a Bloomberg news ribbon, makes the page feel
 *  alive even when nothing is happening (drifts left at a slow pace
 *  when there are no new events). */
export function LiveTicker() {
  const { events } = useEventStream(40);
  const [now, setNow] = useState(() => Date.now());
  const lastSeenRef = useRef<number>(0);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, []);

  // Build a fixed-length scrolling list — drop the oldest, prepend the latest.
  const items = events.slice(-12).reverse();
  const last = events[events.length - 1];
  if (last) lastSeenRef.current = new Date(last.ts).getTime();
  const idleSec = Math.max(0, Math.floor((now - lastSeenRef.current) / 1000));

  return (
    <div
      className="flex items-center gap-4 px-4 py-2 mb-6 overflow-hidden whitespace-nowrap"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      <span
        className="text-[10px] uppercase tracking-[0.16em] shrink-0"
        style={{ color: idleSec < 30 ? "var(--color-accent)" : "var(--color-text-muted)" }}
      >
        ● live
      </span>
      <span style={{ color: "var(--color-border-strong)" }}>│</span>
      <div className="flex-1 overflow-hidden relative">
        <div
          className="flex gap-6"
          style={{
            animation: events.length > 6 ? "ticker-drift 40s linear infinite" : "none",
          }}
        >
          <style>{`
            @keyframes ticker-drift {
              0%   { transform: translateX(0); }
              100% { transform: translateX(-50%); }
            }
          `}</style>
          {[...items, ...items].map((e, i) => (
            <TickerItem key={i} event={e} />
          ))}
        </div>
      </div>
      <span style={{ color: "var(--color-border-strong)" }}>│</span>
      <span
        className="text-[10px] uppercase tracking-[0.16em] shrink-0"
        style={{ color: "var(--color-text-faint)" }}
      >
        {idleSec < 60 ? `${idleSec}s` : `${Math.floor(idleSec / 60)}m`} idle
      </span>
    </div>
  );
}

function TickerItem({ event }: { event: { event: string; ts: string; tool?: string; result?: string } }) {
  const time = new Date(event.ts);
  const hh = pad(time.getHours());
  const mm = pad(time.getMinutes());
  const ss = pad(time.getSeconds());
  const isErr = event.event === "tool_result" && typeof event.result === "string" && event.result.startsWith("ERROR");
  const dot =
    event.event === "tool_call" ? "→"
      : event.event === "tool_result" ? (isErr ? "✗" : "←")
      : event.event === "llm_call" ? "λ"
      : event.event === "assistant_reply" ? "›" : "·";
  const dotColor = isErr ? "var(--color-danger)" : "var(--color-accent)";
  return (
    <span style={{ color: "var(--color-text-muted)" }}>
      <span style={{ color: "var(--color-text-faint)", marginRight: 8 }}>{hh}:{mm}:{ss}</span>
      <span style={{ color: dotColor, marginRight: 6 }}>{dot}</span>
      <span style={{ color: "var(--color-text-dim)" }}>
        {event.tool ?? event.event}
      </span>
    </span>
  );
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}
