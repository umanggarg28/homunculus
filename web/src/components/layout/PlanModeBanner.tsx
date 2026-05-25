import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Brutalist plan-mode banner — hairline strip in amber. */
export function PlanModeBanner() {
  const [isPlan, setIsPlan] = useState(false);

  useEffect(() => {
    const tick = () =>
      api.modeGet().then((r) => setIsPlan(r.mode === "plan")).catch(() => undefined);
    tick();
    const id = setInterval(tick, 5_000);
    return () => clearInterval(id);
  }, []);

  if (!isPlan) return null;

  return (
    <div
      className="px-10 py-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]"
      style={{
        background: "var(--color-bg)",
        borderBottom: "1px solid var(--color-amber)",
        color: "var(--color-text-dim)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <span style={{ color: "var(--color-amber)" }}>● plan mode</span>
      <span style={{ color: "var(--color-border-strong)" }}>──</span>
      <span>read-only · agent will describe what it would do, not execute</span>
    </div>
  );
}
