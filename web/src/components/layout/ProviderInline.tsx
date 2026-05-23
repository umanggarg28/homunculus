import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** Compact provider readout for the sidebar footer. */
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
      <div className="text-[11px] text-[var(--color-text-faint)]">
        No model calls yet
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5">
      <div
        className="text-[11.5px] font-medium truncate"
        style={{ color: "var(--color-text-dim)", fontFamily: "var(--font-mono)" }}
      >
        {active.model}
      </div>
      <div
        className="text-[10.5px] truncate"
        style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
      >
        via {active.host}
      </div>
    </div>
  );
}
