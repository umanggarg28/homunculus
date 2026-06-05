import { motion } from "framer-motion";
import { useMemo } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { Tooltip } from "@/components/ui/Tooltip";

/** A living pulse next to the brand mark.
 *
 *  Calm steady breathing when idle; intensifies (faster + brighter)
 *  when the agent acted recently. The signature element of the
 *  dashboard — always visible, always reassuring you the creature
 *  is alive. */
export function HeartbeatPulse() {
  const { events } = useEventStream(40);

  const { active, lastSeconds } = useMemo(() => {
    const now = Date.now();
    const recent = events
      .filter((e) => e.event === "tool_call" || e.event === "assistant_reply" || e.event === "llm_call")
      .sort((a, b) => (a.ts > b.ts ? -1 : 1));
    const last = recent[0];
    if (!last) return { active: false, lastSeconds: null as number | null };
    const ageS = Math.floor((now - new Date(last.ts).getTime()) / 1000);
    return { active: ageS < 30, lastSeconds: ageS };
  }, [events]);

  // Calm = 2.4s breath. Active = 0.9s strong pulse.
  const period = active ? 0.9 : 2.4;
  const scaleRange = active ? [1, 1.6, 1] : [1, 1.25, 1];
  const opacityRange = active ? [1, 0.55, 1] : [0.7, 0.95, 0.7];

  const tip = lastSeconds === null
    ? <><strong>idle</strong> — no agent activity yet. Pulses when a tool runs, the LLM is called, or the agent replies.</>
    : <>last agent action <strong>{lastSeconds}s ago</strong>. Pulses faster while the agent is actively working.</>;

  return (
    <Tooltip text={tip} placement="bottom">
      <div className="relative w-3 h-3 flex items-center justify-center hm-info hm-info--bare">
        {/* Outer halo — softer, slower */}
        <motion.span
          aria-hidden
          className="absolute inset-0 rounded-full"
          style={{ background: "var(--color-accent)", filter: "blur(4px)" }}
          animate={{
            scale: active ? [1, 2.4, 1] : [1, 1.7, 1],
            opacity: active ? [0.5, 0, 0.5] : [0.3, 0, 0.3],
          }}
          transition={{ duration: period * 1.4, repeat: Infinity, ease: "easeOut" }}
        />
        {/* Core dot */}
        <motion.span
          className="relative inline-block w-1.5 h-1.5 rounded-full"
          style={{
            background: "var(--color-accent)",
            boxShadow: active
              ? "0 0 6px 1px rgba(94,234,212,0.85), 0 0 16px 2px rgba(94,234,212,0.45)"
              : "0 0 4px 0 rgba(94,234,212,0.55)",
          }}
          animate={{ scale: scaleRange, opacity: opacityRange }}
          transition={{ duration: period, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>
    </Tooltip>
  );
}
