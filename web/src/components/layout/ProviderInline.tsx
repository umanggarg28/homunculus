import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** Sidebar footer model readout — brutalist mono. */
export function ProviderInline() {
  const { events } = useEventStream(10);
  const [active, setActive] = useState<{ model: string; host: string } | null>(null);

  useEffect(() => {
    const llmCall = [...events].reverse().find((e) => e.event === "llm_call");
    if (!llmCall || !llmCall.model) return;
    setActive({ model: llmCall.model, host: llmCall.host ?? "" });
  }, [events]);

  if (!active) {
    return (
      <div
        className="text-[10px] uppercase tracking-[0.14em]"
        style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
      >
        ─ no calls yet
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5" style={{ fontFamily: "var(--font-mono)" }}>
      <div className="text-[10.5px] truncate" style={{ color: "var(--color-text-dim)" }}>
        {active.model}
      </div>
      <div
        className="text-[9px] uppercase tracking-[0.16em] truncate"
        style={{ color: "var(--color-text-faint)" }}
      >
        via {active.host}
      </div>
    </div>
  );
}
