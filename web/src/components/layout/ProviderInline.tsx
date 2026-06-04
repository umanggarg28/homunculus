import { useEffect, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { Tooltip } from "@/components/ui/Tooltip";

interface ModelInfo { model: string; host: string; is_fallback?: boolean; }

/** Sidebar footer model readout — brutalist mono. */
export function ProviderInline() {
  const { events } = useEventStream(10);
  const [info, setInfo] = useState<ModelInfo | null>(null);

  // Fetch on mount — API now returns the last actually-used model from
  // events.jsonl, not just the configured primary.
  useEffect(() => {
    fetch("/api/model")
      .then((r) => r.json())
      .then((d) => setInfo({ model: d.model ?? "", host: d.host ?? "", is_fallback: d.is_fallback ?? false }))
      .catch(() => {});
  }, []);

  // Override with live SSE events as they arrive.
  useEffect(() => {
    const llmCall = [...events].reverse().find((e) => e.event === "llm_call");
    if (!llmCall?.model) return;
    const primary = info?.model;
    setInfo({ model: llmCall.model, host: llmCall.host ?? "", is_fallback: llmCall.model !== primary });
  }, [events]);

  if (!info) {
    return (
      <div
        className="text-[10px] uppercase tracking-[0.14em]"
        style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
      >
        ─ loading…
      </div>
    );
  }

  const fallback = info.is_fallback;

  const tip = (
    <>
      <strong>{info.model}</strong>
      {info.host ? <> · via {info.host}</> : null}
      <br />
      {fallback
        ? <>Primary model is rate-limited; serving from a fallback provider in the configured chain.</>
        : <>Current model serving chat + heartbeat. Swap mid-session with <kbd>/use &lt;model&gt;</kbd>.</>}
    </>
  );

  return (
    <Tooltip text={tip} placement="top">
      <div className="flex flex-col gap-0.5" style={{ fontFamily: "var(--font-mono)", cursor: "help" }}>
        {fallback && (
          <div className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--color-warn, #f59e0b)" }}>
            ⚠ fallback active
          </div>
        )}
        <div
          className="text-[10.5px] truncate"
          style={{ color: fallback ? "var(--color-warn, #f59e0b)" : "var(--color-text-dim)" }}
        >
          {info.model}
        </div>
        {info.host && (
          <div
            className="text-[9px] uppercase tracking-[0.16em] truncate"
            style={{ color: "var(--color-text-faint)" }}
          >
            via {info.host}
          </div>
        )}
      </div>
    </Tooltip>
  );
}
