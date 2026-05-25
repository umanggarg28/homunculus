import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { ToolCallEntry } from "./ToolCallCard";

/** Tool-call rendered as a bordered receipt panel.
 *
 *  TOOL_NAME ──────────────── [ OK · 412MS ]
 *  ┃  URL   https://…
 *  ┃  ↩     <result preview>
 *                              [EXPAND ▼]
 *
 *  Click to expand/collapse result.
 */
export function BrutalistToolBlock({ entry }: { entry: ToolCallEntry }) {
  const [expanded, setExpanded] = useState(false);
  const inFlight = entry.result === undefined;
  const duration = entry.durationMs ?? Math.max(0, Date.now() - entry.startedAt);
  const isError = !inFlight && /^error/i.test(entry.result ?? "");

  const accentColor = inFlight
    ? "var(--color-amber)"
    : isError
      ? "var(--color-danger)"
      : "var(--color-border-bright)";

  const statusColor = inFlight
    ? "var(--color-amber)"
    : isError
      ? "var(--color-danger)"
      : "var(--color-accent)";

  const statusLabel = inFlight ? "RUNNING" : isError ? "ERR" : "OK";
  const args = parseArgs(entry.args);

  return (
    <div
      className="mb-2 cursor-pointer"
      onClick={() => !inFlight && setExpanded((v) => !v)}
      style={{ fontFamily: "var(--font-mono)" }}
    >
      {/* Header row */}
      <div
        className="flex items-center gap-2 mb-0"
        style={{
          borderBottom: `1px solid ${accentColor}`,
          paddingBottom: 6,
          marginBottom: 0,
        }}
      >
        {/* Tool name */}
        <span
          className="text-[10px] tracking-[0.18em] uppercase font-semibold flex-1 truncate"
          style={{ color: statusColor }}
        >
          {entry.name}
        </span>

        {/* Status badge */}
        <span
          className="text-[9px] tracking-[0.18em] uppercase font-bold shrink-0"
          style={{
            color: statusColor,
            border: `1px solid ${statusColor}`,
            padding: "1px 7px",
            background: inFlight ? "rgba(255,176,0,0.06)" : "transparent",
          }}
        >
          {statusLabel} · {formatDuration(duration)}
        </span>
      </div>

      {/* Args */}
      <div
        style={{
          borderLeft: `1px solid ${accentColor}`,
          marginLeft: 0,
          paddingLeft: 10,
          paddingTop: 6,
          paddingBottom: 4,
          background: "linear-gradient(90deg, rgba(0,0,0,0.2) 0%, transparent 40%)",
        }}
      >
        {args.length === 0 ? (
          <div className="text-[11px]" style={{ color: "var(--color-text-faint)" }}>
            no args
          </div>
        ) : (
          args.map(([k, v]) => (
            <div
              key={k}
              className="flex gap-3 text-[11.5px] leading-[1.55] mb-[2px]"
            >
              <span
                className="shrink-0 uppercase tracking-[0.14em] text-[10px] font-semibold"
                style={{ color: "var(--color-accent)", width: 72, paddingTop: 1 }}
              >
                {k}
              </span>
              <span
                className="flex-1 break-all"
                style={{
                  color: "var(--color-text-dim)",
                  whiteSpace: expanded ? "pre-wrap" : "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {expanded ? v : compactPreview(v, 120)}
              </span>
            </div>
          ))
        )}

        {/* Result — animated expand */}
        {entry.result !== undefined && entry.result && (
          <>
            <AnimatePresence>
              {expanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                  style={{ overflow: "hidden" }}
                >
                  <div
                    className="mt-2 pt-2"
                    style={{ borderTop: "1px dashed var(--color-border)" }}
                  >
                    <span
                      className="text-[10px] uppercase tracking-[0.14em] font-semibold block mb-1"
                      style={{ color: isError ? "var(--color-danger)" : "var(--color-accent)" }}
                    >
                      result
                    </span>
                    <pre
                      className="text-[11.5px] leading-[1.55] whitespace-pre-wrap break-words"
                      style={{
                        color: isError ? "var(--color-danger)" : "var(--color-text-dim)",
                        background: "rgba(0,0,0,0.4)",
                        border: "1px solid var(--color-border)",
                        padding: "8px 10px",
                        maxHeight: 280,
                        overflow: "auto",
                        fontFamily: "var(--font-mono)",
                        margin: 0,
                      }}
                    >
                      {entry.result}
                    </pre>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {!expanded && (
              <div className="mt-1 flex gap-3 text-[11.5px] leading-[1.55]">
                <span
                  className="shrink-0 uppercase tracking-[0.14em] text-[10px] font-semibold"
                  style={{ color: isError ? "var(--color-danger)" : "var(--color-accent)", width: 72, paddingTop: 1 }}
                >
                  result
                </span>
                <span
                  className="flex-1 truncate"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {compactPreview(entry.result, 120)}
                </span>
              </div>
            )}
          </>
        )}
      </div>

      {/* Expand/collapse hint — only when there's a result */}
      {!inFlight && entry.result && (
        <div
          className="text-[9px] uppercase tracking-[0.22em] text-right pt-1"
          style={{ color: "var(--color-text-faint)" }}
        >
          {expanded ? "▲ collapse" : "▼ expand"}
        </div>
      )}
    </div>
  );
}

function parseArgs(raw: string | undefined): Array<[string, string]> {
  if (!raw) return [];
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      return Object.entries(obj).map(([k, v]) => [
        k,
        typeof v === "string" ? v : JSON.stringify(v),
      ]);
    }
  } catch { /* fall through */ }
  const collapsed = raw.replace(/\s+/g, " ").trim();
  return collapsed ? [["raw", collapsed]] : [];
}

function compactPreview(s: string, n: number): string {
  if (!s) return "";
  const collapsed = s.replace(/\s+/g, " ").trim();
  return collapsed.length <= n ? collapsed : collapsed.slice(0, n) + "…";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
