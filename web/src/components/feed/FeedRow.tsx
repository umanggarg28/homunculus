import { useState } from "react";
import type { FeedEvent } from "@/lib/types";

interface Props { event: FeedEvent; }

const KIND_COLOR: Record<string, string> = {
  user_message:     "var(--color-text)",
  assistant_reply:  "var(--color-accent)",
  tool_call:        "var(--color-accent)",
  tool_result:      "var(--color-text-dim)",
  llm_call:         "var(--color-amber)",
  output_guard:     "var(--color-danger)",
  self_correction:  "var(--color-warning)",
  memory_write:     "#818cf8",
  memory_forget:    "var(--color-text-faint)",
};

const KIND_GLYPH: Record<string, string> = {
  user_message:     "$",
  assistant_reply:  "›",
  tool_call:        "→",
  tool_result:      "←",
  llm_call:         "λ",
  output_guard:     "⚠",
  self_correction:  "↺",
  memory_write:     "✦",
  memory_forget:    "✕",
};

const COMPACT_LEN = 280;

/** Brutalist trace row with click-to-expand full detail.
 *
 *  Truncated rows show a bright "[N chars more ▼]" button so the
 *  user can always see whether the event was cut or complete.
 *  Errors get a danger-red left border instead of just red text.
 */
export function FeedRow({ event: e }: Props) {
  const [expanded, setExpanded] = useState(false);

  const isErr = (e.event === "tool_result" && typeof e.result === "string" && e.result.startsWith("ERROR"))
    || e.event === "output_guard";

  const isNotifyQueued = e.event === "tool_result" && e.name === "notify"
    && typeof e.result === "string" && e.result.startsWith("Notification queued");
  const isNotifyDelivered = e.event === "tool_result" && e.name === "notify"
    && typeof e.result === "string" && e.result.startsWith("Notification delivered");

  const dotColor = isErr
    ? "var(--color-danger)"
    : isNotifyQueued ? "var(--color-amber)"
    : isNotifyDelivered ? "#4ade80"
    : KIND_COLOR[e.event] ?? "var(--color-text-muted)";
  const glyph = isErr ? "✗"
    : isNotifyQueued ? "⏸"
    : isNotifyDelivered ? "✓"
    : KIND_GLYPH[e.event] ?? "·";

  const fullDetail = getFullDetail(e);
  const isTruncatable = fullDetail.length > COMPACT_LEN;
  const displayDetail = expanded || !isTruncatable
    ? fullDetail
    : fullDetail.slice(0, COMPACT_LEN);

  return (
    <div
      className="grid py-1.5 px-4"
      onClick={isTruncatable ? () => setExpanded((v) => !v) : undefined}
      style={{
        gridTemplateColumns: "90px 80px 18px 150px 1fr",
        gap: "0 16px",
        alignItems: "start",
        borderBottom: "1px solid var(--color-border)",
        borderLeft: isErr ? "2px solid var(--color-danger)" : "2px solid transparent",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        background: isErr ? "rgba(255,77,46,0.04)" : "transparent",
        cursor: isTruncatable ? "pointer" : "default",
      }}
    >
      {/* Time */}
      <span
        className="pt-[2px]"
        style={{ color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums" }}
      >
        {formatHms(e.ts)}
      </span>

      {/* Service */}
      <span
        className="uppercase tracking-[0.12em] pt-[2px]"
        style={{ color: "var(--color-text-muted)", fontSize: 10 }}
      >
        {e.service}
      </span>

      {/* Glyph */}
      <span className="pt-[2px]" style={{ color: dotColor }}>{glyph}</span>

      {/* Kind + tool name (two lines so the name is always visible) */}
      <div className="pt-[2px]" style={{ minWidth: 0 }}>
        <div
          className="uppercase tracking-[0.12em]"
          style={{ color: dotColor, fontSize: 10 }}
        >
          {e.event.replace(/_/g, " ")}
        </div>
        {e.name && (
          <div
            className="tracking-[0.06em]"
            style={{
              color: "var(--color-text-muted)",
              fontSize: 10,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {e.name}
          </div>
        )}
      </div>

      {/* Detail */}
      <div style={{ minWidth: 0 }}>
        <span
          className="break-words whitespace-pre-wrap"
          style={{ color: isErr ? "var(--color-danger)" : isNotifyQueued ? "var(--color-amber)" : isNotifyDelivered ? "#4ade80" : "var(--color-text-dim)" }}
        >
          {displayDetail}
        </span>

        {isTruncatable && (
          <span
            className="text-[10px] uppercase tracking-[0.14em]"
            style={{
              display: "block",
              marginTop: 4,
              color: "var(--color-accent)",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.14em",
            }}
          >
            {expanded ? "▲ collapse" : `▼ +${fullDetail.length - COMPACT_LEN} chars`}
          </span>
        )}
      </div>
    </div>
  );
}

/** Returns the complete detail string for this event (no truncation). */
function getFullDetail(e: FeedEvent): string {
  switch (e.event) {
    case "user_message":    return e.text ?? "";
    case "assistant_reply": return e.text ?? "";
    case "tool_call":       return formatArgs(e.args);
    case "tool_result":     return e.result ?? "";
    case "llm_call": {
      const tokenParts: string[] = [];
      if (e.input_tokens != null) tokenParts.push(`${e.input_tokens}in`);
      if (e.output_tokens != null) tokenParts.push(`${e.output_tokens}out`);
      if (e.cached_tokens) tokenParts.push(`${e.cached_tokens}cached`);
      const tokenStr = tokenParts.length ? `  [${tokenParts.join(" · ")}]` : "";
      const header = `${e.model ?? ""} via ${e.host ?? ""}${tokenStr}`;
      if (!e.request) return header;
      try {
        const parsed = JSON.parse(e.request);
        return `${header}\n\n${JSON.stringify(parsed, null, 2)}`;
      } catch {
        return `${header}\n\n${e.request}`;
      }
    }
    case "output_guard":    return e.text ?? e.result ?? "reply blocked by output guard";
    case "self_correction": return `correcting: ${e.text ?? ""} | was: ${e.result ?? ""}`;
    case "memory_write":    return `${(e as FeedEvent & { action?: string }).action ?? "saved"}: ${(e as FeedEvent & { memory_name?: string; description?: string }).memory_name ?? e.name ?? ""} — ${(e as FeedEvent & { description?: string }).description ?? ""}`;
    case "memory_forget":   return e.text ?? e.name ?? "";
    default:                return JSON.stringify(e, null, 2);
  }
}

/** Pretty-print JSON args if possible, else return raw string. */
function formatArgs(raw: string | undefined): string {
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object") {
      return Object.entries(obj)
        .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join("\n");
    }
  } catch { /* fall through */ }
  return raw;
}

function formatHms(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}
