import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

export interface ToolCallEntry {
  name: string;
  args: string;
  result?: string;
  durationMs?: number;
  startedAt: number;
}

interface Props {
  entry: ToolCallEntry;
}

/** A single tool call rendered inline in the assistant turn.
 *
 * Collapsed by default: shows the tool name, a one-line preview of
 * args, and elapsed time. Click to expand for full args + result. */
export function ToolCallCard({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);
  const inFlight = entry.result === undefined;

  const previewArgs = compactPreview(entry.args, 60);
  const previewResult = compactPreview(entry.result ?? "", 60);
  const duration = entry.durationMs ?? Math.max(0, Date.now() - entry.startedAt);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className={`
        my-2 border-l-2 pl-3 cursor-pointer select-none
        ${inFlight
          ? "border-[var(--color-signal)]"
          : "border-[var(--color-border-strong)] hover:border-[var(--color-text-dim)]"}
        transition-colors
      `}
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-baseline gap-3 mono-caps text-[var(--color-text-dim)]">
        <span className={inFlight ? "text-[var(--color-signal)]" : "text-[var(--color-text)]"}>
          ↳ {entry.name}
        </span>
        <span className="text-[var(--color-text-muted)] truncate flex-1 normal-case tracking-normal">
          {previewArgs}
        </span>
        <span className="text-[var(--color-text-faint)]">
          {inFlight ? formatLive(duration) : formatFinal(duration)}
        </span>
      </div>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="expanded"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <div className="mt-2 mb-1 text-[var(--color-text-dim)] leading-relaxed">
              <Pane label="args">{entry.args || "—"}</Pane>
              {entry.result !== undefined && (
                <Pane label="result">{entry.result || "—"}</Pane>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {!expanded && entry.result !== undefined && previewResult && (
        <div className="text-[var(--color-text-muted)] truncate mt-0.5">
          ↩ {previewResult}
        </div>
      )}
    </motion.div>
  );
}

function Pane({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 first:mt-2">
      <div className="mono-caps text-[var(--color-text-faint)] mb-1">{label}</div>
      <pre className="
        whitespace-pre-wrap break-words
        text-[12px] text-[var(--color-text-dim)]
        bg-[var(--color-surface-3)]/40
        border border-[var(--color-border)]
        rounded-md px-3 py-2
      ">{children}</pre>
    </div>
  );
}

function compactPreview(s: string, n: number): string {
  if (!s) return "";
  const collapsed = s.replace(/\s+/g, " ").trim();
  if (collapsed.length <= n) return collapsed;
  return collapsed.slice(0, n) + "…";
}

function formatLive(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s ▸`;
}
function formatFinal(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
