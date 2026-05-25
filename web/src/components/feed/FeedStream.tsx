import { useEffect, useRef } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { FeedRow } from "./FeedRow";

/** Brutalist trace stream — hairline-bordered container, no card. */
export function FeedStream() {
  const { events } = useEventStream(300);
  const endRef = useRef<HTMLDivElement>(null);

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
    <div style={{ border: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}>
      {events.map((e, i) => (
        <FeedRow key={`${e.ts}-${i}`} event={e} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
