import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

export function ThinkingIndicator({ active }: { active: boolean }) {
  const { events } = useEventStream(30);
  const [currentTool, setCurrentTool] = useState<string | null>(null);

  useEffect(() => {
    if (!active) { setCurrentTool(null); return; }
    const recent = [...events].reverse();
    for (const e of recent) {
      if (e.service !== "feed") continue;
      if (e.event === "tool_result") { setCurrentTool(null); return; }
      if (e.event === "tool_call" && e.name) { setCurrentTool(e.name); return; }
    }
  }, [events, active]);

  if (!active) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className="flex items-center gap-2 mt-3"
    >
      <motion.span
        aria-hidden
        className="inline-block w-1.5 h-1.5 rounded-full"
        style={{ background: "var(--color-accent)" }}
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
      />
      <span className="text-[12px] text-[var(--color-text-muted)]">
        {currentTool ? (
          <>Running <span style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-dim)" }}>{currentTool}</span>…</>
        ) : "Thinking…"}
      </span>
    </motion.div>
  );
}
