import { useMemo } from "react";
import { motion } from "framer-motion";
import type { FeedEvent } from "@/lib/types";

interface Props { events: FeedEvent[]; hours?: number; }

interface Bin {
  ts: number;
  tools: number;
  llm: number;
  user: number;
}

const COLORS = {
  tools:  "var(--color-accent)",
  llm:    "var(--color-amber)",
  user:   "var(--color-indigo)",
};

/** A 24h activity ribbon. Each bar = 15 minutes. Stacked by event
 *  type. Current 15-min window glows. Hover reveals timestamp + counts.
 *  The dashboard's signature visualisation. */
export function HeartbeatRibbon({ events, hours = 24 }: Props) {
  const { bins, maxTotal, now } = useMemo(() => {
    const binSizeMs = 15 * 60 * 1000;
    const totalBins = Math.ceil((hours * 60) / 15);
    const now = Math.floor(Date.now() / binSizeMs) * binSizeMs;
    const start = now - (totalBins - 1) * binSizeMs;

    const bins: Bin[] = Array.from({ length: totalBins }, (_, i) => ({
      ts: start + i * binSizeMs,
      tools: 0,
      llm: 0,
      user: 0,
    }));

    for (const e of events) {
      const t = new Date(e.ts).getTime();
      if (t < start) continue;
      const idx = Math.floor((t - start) / binSizeMs);
      if (idx < 0 || idx >= bins.length) continue;
      if (e.event === "tool_call" || e.event === "assistant_reply") bins[idx].tools++;
      else if (e.event === "llm_call") bins[idx].llm++;
      else if (e.event === "user_message") bins[idx].user++;
    }

    const maxTotal = Math.max(1, ...bins.map((b) => b.tools + b.llm + b.user));
    return { bins, maxTotal, now };
  }, [events, hours]);

  return (
    <div
      className="rounded-[10px] p-5"
      style={{
        background:
          "linear-gradient(180deg, var(--color-surface-2) 0%, var(--color-surface-1) 100%)",
        border: "1px solid var(--color-border)",
      }}
    >
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Heartbeat
          </div>
          <div
            className="readout mt-1 text-[var(--color-text)]"
            style={{ fontSize: 22 }}
          >
            Last 24 hours
          </div>
        </div>
        <Legend />
      </div>

      <div
        className="flex items-end gap-[2px]"
        style={{ height: 84 }}
      >
        {bins.map((b, i) => {
          const total = b.tools + b.llm + b.user;
          const heightPct = total === 0 ? 4 : 8 + (total / maxTotal) * 92;
          const isCurrent = i === bins.length - 1;
          const isEmpty = total === 0;
          return (
            <motion.div
              key={b.ts}
              initial={{ height: 0 }}
              animate={{ height: `${heightPct}%` }}
              transition={{ duration: 0.4, delay: Math.min(i * 0.005, 0.3), ease: [0.2, 0.8, 0.2, 1] }}
              className="flex-1 flex flex-col-reverse rounded-[2px] overflow-hidden relative group"
              style={{
                background: isEmpty ? "rgba(255,255,255,0.04)" : "transparent",
                minHeight: 4,
              }}
              title={tooltipFor(b, isCurrent ? now : b.ts)}
            >
              {b.tools > 0 && (
                <div style={{ flex: b.tools, background: COLORS.tools, opacity: isCurrent ? 1 : 0.85 }} />
              )}
              {b.llm > 0 && (
                <div style={{ flex: b.llm, background: COLORS.llm, opacity: isCurrent ? 1 : 0.85 }} />
              )}
              {b.user > 0 && (
                <div style={{ flex: b.user, background: COLORS.user, opacity: isCurrent ? 1 : 0.85 }} />
              )}
              {isCurrent && total > 0 && (
                <motion.div
                  className="absolute inset-0 rounded-[2px] pointer-events-none"
                  style={{ boxShadow: "0 0 8px 1px rgba(94,234,212,0.7)" }}
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                />
              )}
            </motion.div>
          );
        })}
      </div>

      <div className="flex justify-between mt-2 text-[10px] tabular text-[var(--color-text-faint)]" style={{ fontFamily: "var(--font-mono)" }}>
        <span>{formatHour(bins[0].ts)}</span>
        <span>{formatHour(bins[Math.floor(bins.length / 2)].ts)}</span>
        <span>now</span>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--color-text-muted)" }}>
      <LegendDot color={COLORS.tools} label="actions" />
      <LegendDot color={COLORS.llm}   label="llm" />
      <LegendDot color={COLORS.user}  label="messages" />
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />
      <span>{label}</span>
    </span>
  );
}

function tooltipFor(b: Bin, ts: number): string {
  const d = new Date(ts);
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const parts: string[] = [time];
  if (b.tools > 0) parts.push(`${b.tools} action${b.tools === 1 ? "" : "s"}`);
  if (b.llm > 0)   parts.push(`${b.llm} llm`);
  if (b.user > 0)  parts.push(`${b.user} msg`);
  return parts.join(" · ");
}

function formatHour(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
