import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import type { ToolCallEntry } from "./ToolCallCard";

interface Props { entries: ToolCallEntry[]; }

/** Tool calls under an assistant message — compact rows, click to expand. */
export function ManuscriptMargin({ entries }: Props) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (!entries.length) return null;

  return (
    <div
      className="rounded-[6px] overflow-hidden mt-2"
      style={{ border: "1px solid var(--color-border)", background: "var(--color-surface-2)" }}
    >
      {entries.map((entry, idx) => {
        const inFlight = entry.result === undefined;
        const expanded = expandedIdx === idx;
        return (
          <div
            key={idx}
            style={{
              borderTop: idx === 0 ? undefined : "1px solid var(--color-border)",
            }}
          >
            <button
              onClick={() => setExpandedIdx(expanded ? null : idx)}
              className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-[var(--color-surface-3)] transition-colors"
            >
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{
                  background: inFlight ? "var(--color-accent)" : "var(--color-success)",
                }}
              />
              <span
                className="text-[12.5px]"
                style={{ fontFamily: "var(--font-mono)", color: "var(--color-text)" }}
              >
                {entry.name}
              </span>
              <span className="ml-auto text-[11px] text-[var(--color-text-muted)] tabular">
                {inFlight ? "running…" : durationLabel(entry.durationMs)}
              </span>
            </button>

            <AnimatePresence initial={false}>
              {expanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden"
                >
                  <div className="px-3 pb-3 pt-1 flex flex-col gap-2">
                    {entry.args && <Pane label="args">{entry.args}</Pane>}
                    {entry.result !== undefined && (
                      <Pane label="result">{entry.result || "—"}</Pane>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}

function Pane({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
        {label}
      </div>
      <pre
        className="whitespace-pre-wrap break-words leading-relaxed rounded-[4px] px-2 py-1.5"
        style={{
          color: "var(--color-text-dim)",
          background: "var(--color-bg)",
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          border: "1px solid var(--color-border)",
        }}
      >
        {children}
      </pre>
    </div>
  );
}

function durationLabel(ms?: number) {
  if (ms === undefined) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
