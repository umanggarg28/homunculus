import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** Hairline red frame around the viewport while the agent is acting
 *  with no human in the loop — i.e. heartbeat-service tool activity
 *  in the last few seconds. Honest by construction: it can only
 *  appear while autonomous events are actually streaming, and fades
 *  the moment they stop.
 *
 *  Chat-driven work doesn't trigger it; you're the one driving.
 */
export function AutonomousFrame() {
  const { events } = useEventStream(40);
  const [active, setActive] = useState(false);

  useEffect(() => {
    const check = () => {
      const now = Date.now();
      const hot = events.some(
        (e) =>
          e.service === "heartbeat" &&
          (e.event === "tool_call" || e.event === "tool_result" || e.event === "llm_call") &&
          now - new Date(e.ts).getTime() < 8000,
      );
      setActive(hot);
    };
    check();
    const t = setInterval(check, 2000);
    return () => clearInterval(t);
  }, [events]);

  if (!active) return null;

  return (
    <div className="hm-autonomous-frame" aria-hidden>
      <span
        style={{
          position: "absolute",
          top: 0,
          left: "50%",
          transform: "translateX(-50%)",
          background: "var(--color-bg)",
          border: "1px solid color-mix(in srgb, var(--color-danger) 55%, transparent)",
          borderTop: "none",
          padding: "2px 10px",
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          letterSpacing: "0.22em",
          color: "var(--color-danger)",
          textTransform: "uppercase",
        }}
      >
        autonomous action in progress
      </span>
    </div>
  );
}
