import { useEffect, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { FeedRow } from "./FeedRow";

// Events that are infrastructure noise — shown only when "system" toggle is on.
const SYSTEM_EVENTS = new Set([
  "service_ping",
  "provider_cooled",
  "context_compacted",
  "budget_blocked",
  "agent_controls_updated",
]);

/** Brutalist trace stream — hairline-bordered container, no card. */
export function FeedStream() {
  const { events } = useEventStream(300);
  const [showSystem, setShowSystem] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const visible = showSystem ? events : events.filter((e) => !SYSTEM_EVENTS.has(e.event));

  // Count how many system events are hidden so the toggle label is informative.
  const hiddenCount = events.length - visible.length;

  useEffect(() => {
    const nearBottom =
      window.innerHeight + window.scrollY > document.body.offsetHeight - 200;
    if (nearBottom) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0) {
    return (
      <div
        className="p-8 text-center text-[11px] uppercase tracking-[0.16em]"
        style={{
          border: "1px solid var(--color-border)",
          color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        ─ waiting for first event ─
      </div>
    );
  }

  return (
    <div>
      {/* System-event toggle */}
      <div
        className="flex items-center gap-3 mb-2"
        style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--color-text-faint)" }}
      >
        <button
          onClick={() => setShowSystem((v) => !v)}
          style={{
            background: "none",
            border: "1px solid var(--color-border)",
            color: showSystem ? "var(--color-accent)" : "var(--color-text-faint)",
            padding: "2px 8px",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.10em",
            textTransform: "uppercase",
          }}
        >
          {showSystem ? "hide system" : "show system"}
        </button>
        {!showSystem && hiddenCount > 0 && (
          <span style={{ letterSpacing: "0.08em" }}>
            {hiddenCount} ping/infra event{hiddenCount !== 1 ? "s" : ""} hidden
          </span>
        )}
      </div>

      <div style={{ border: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}>
        {visible.length === 0 ? (
          <div
            className="p-8 text-center text-[11px] uppercase tracking-[0.16em]"
            style={{ color: "var(--color-text-muted)" }}
          >
            ─ no agent activity yet ─
          </div>
        ) : (
          visible.map((e, i) => <FeedRow key={`${e.ts}-${i}`} event={e} />)
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
