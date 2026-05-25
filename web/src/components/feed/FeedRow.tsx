import type { FeedEvent } from "@/lib/types";

interface Props { event: FeedEvent; }

const KIND_COLOR: Record<string, string> = {
  user_message:     "var(--color-text)",
  assistant_reply:  "var(--color-accent)",
  tool_call:        "var(--color-accent)",
  tool_result:      "var(--color-text-dim)",
  llm_call:         "var(--color-amber)",
};

const KIND_GLYPH: Record<string, string> = {
  user_message:     "$",
  assistant_reply:  "›",
  tool_call:        "→",
  tool_result:      "←",
  llm_call:         "λ",
};

/** Brutalist trace row — `HH:MM:SS · SERVICE · GLYPH · KIND · detail`,
 *  full mono, color-coded glyph, hairline between rows. */
export function FeedRow({ event: e }: Props) {
  const isErr = e.event === "tool_result" && typeof e.result === "string" && e.result.startsWith("ERROR");
  const dotColor = isErr ? "var(--color-danger)" : KIND_COLOR[e.event] ?? "var(--color-text-muted)";
  const glyph = isErr ? "✗" : KIND_GLYPH[e.event] ?? "·";
  return (
    <div
      className="grid items-baseline gap-4 py-1.5 px-4"
      style={{
        gridTemplateColumns: "90px 80px 18px 110px 1fr",
        borderBottom: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
      }}
    >
      <span
        style={{ color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums" }}
      >
        {formatHms(e.ts)}
      </span>
      <span
        className="uppercase tracking-[0.12em]"
        style={{ color: "var(--color-text-muted)", fontSize: 10 }}
      >
        {e.service}
      </span>
      <span style={{ color: dotColor }}>{glyph}</span>
      <span
        className="uppercase tracking-[0.12em]"
        style={{ color: dotColor, fontSize: 10 }}
      >
        {e.event.replace(/_/g, " ")}
        {e.name ? <span style={{ color: "var(--color-text-muted)", marginLeft: 6 }}>· {e.name}</span> : null}
      </span>
      <span
        className="break-words whitespace-pre-wrap"
        style={{ color: isErr ? "var(--color-danger)" : "var(--color-text-dim)" }}
      >
        {renderEventDetail(e)}
      </span>
    </div>
  );
}

function renderEventDetail(e: FeedEvent): string {
  switch (e.event) {
    case "user_message":     return e.text ?? "";
    case "assistant_reply":  return truncate(e.text ?? "", 200);
    case "tool_call":        return truncate(e.args ?? "", 200);
    case "tool_result":      return truncate(e.result ?? "", 200);
    case "llm_call":         return `${e.model ?? ""} via ${e.host ?? ""}`;
    default:                 return JSON.stringify(e);
  }
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function formatHms(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}
