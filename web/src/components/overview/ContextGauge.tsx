import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

interface CtxData {
  used_tokens: number;
  limit_tokens: number;
  model: string;
  pct: number;
}

/** Horizontal fill gauge showing how much of the model's context window
 *  is currently consumed. Refreshes on each llm_call SSE event. */
export function ContextGauge() {
  const [data, setData] = useState<CtxData | null>(null);
  const { events } = useEventStream(20);

  useEffect(() => {
    api.contextGauge().then(setData).catch(() => undefined);
  }, []);

  // Refresh whenever a new llm_call lands in the feed.
  const llmCallCount = events.filter((e) => e.event === "llm_call").length;
  useEffect(() => {
    if (llmCallCount === 0) return;
    api.contextGauge().then(setData).catch(() => undefined);
  }, [llmCallCount]);

  if (!data || data.used_tokens === 0) return null;

  const pct = Math.min(data.pct, 100);
  const barColor = pct >= 90
    ? "var(--color-danger)"
    : pct >= 70
    ? "var(--color-amber)"
    : "var(--color-accent)";

  const fmtK = (n: number) =>
    n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n);

  return (
    <div
      className="px-5 py-3 mb-6"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <div className="flex items-center gap-4 mb-2">
        <span
          className="text-[10px] uppercase tracking-[0.22em] shrink-0"
          style={{ color: "var(--color-text-muted)" }}
        >
          ── context window
        </span>
        <span
          className="text-[10px] uppercase tracking-[0.12em] ml-auto"
          style={{ color: "var(--color-text-faint)" }}
        >
          {fmtK(data.used_tokens)} / {fmtK(data.limit_tokens)} · {pct.toFixed(1)}%
        </span>
      </div>

      {/* Bar */}
      <div
        style={{
          height: 3,
          background: "var(--color-border)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            height: "100%",
            width: `${pct}%`,
            background: barColor,
            transition: "width 0.4s ease, background 0.3s",
          }}
        />
      </div>

      <div
        className="text-[10px] uppercase tracking-[0.10em] mt-1.5"
        style={{ color: "var(--color-text-faint)" }}
      >
        {data.model}
      </div>
    </div>
  );
}
