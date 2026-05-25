import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** Sidebar footer model readout — brutalist mono. */
export function ProviderInline() {
  const { events } = useEventStream(10);
  const [active, setActive] = useState<{ model: string; host: string } | null>(null);
  const [configured, setConfigured] = useState<{ model: string; host: string } | null>(null);

  // Fetch the configured model from the backend on mount so we show it
  // immediately without waiting for a live LLM call.
  useEffect(() => {
    fetch("/api/model")
      .then((r) => r.json())
      .then((d) => setConfigured({ model: d.model ?? "", host: d.host ?? "" }))
      .catch(() => {});
  }, []);

  // Once a real call happens, prefer that (shows the actual model used,
  // which may differ when a provider fell back).
  useEffect(() => {
    const llmCall = [...events].reverse().find((e) => e.event === "llm_call");
    if (!llmCall || !llmCall.model) return;
    setActive({ model: llmCall.model, host: llmCall.host ?? "" });
  }, [events]);

  const display = active ?? configured;

  if (!display) {
    return (
      <div
        className="text-[10px] uppercase tracking-[0.14em]"
        style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
      >
        ─ loading…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5" style={{ fontFamily: "var(--font-mono)" }}>
      <div className="text-[10.5px] truncate" style={{ color: "var(--color-text-dim)" }}>
        {display.model}
      </div>
      {display.host && (
        <div
          className="text-[9px] uppercase tracking-[0.16em] truncate"
          style={{ color: "var(--color-text-faint)" }}
        >
          via {display.host}
        </div>
      )}
    </div>
  );
}
