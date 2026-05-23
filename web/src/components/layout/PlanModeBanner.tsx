import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function PlanModeBanner() {
  const [isPlan, setIsPlan] = useState(false);

  useEffect(() => {
    const tick = () =>
      api.modeGet().then((r) => setIsPlan(r.mode === "plan")).catch(() => undefined);
    tick();
    const id = setInterval(tick, 5_000);
    return () => clearInterval(id);
  }, []);

  return (
    <AnimatePresence>
      {isPlan && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.2 }}
          style={{
            background: "var(--color-accent-faint)",
            borderBottom: "1px solid rgba(99,102,241,0.32)",
            overflow: "hidden",
          }}
        >
          <div className="px-8 py-2 flex items-center gap-2.5">
            <span
              className="text-[11px] font-semibold uppercase tracking-wider"
              style={{ color: "var(--color-accent)" }}
            >
              Plan mode
            </span>
            <span
              className="text-[13px]"
              style={{ color: "var(--color-text-dim)" }}
            >
              Read-only. The agent will describe what it would do, not execute.
            </span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
