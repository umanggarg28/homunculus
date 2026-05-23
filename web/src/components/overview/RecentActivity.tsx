import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import type { FeedEvent } from "@/lib/types";

interface Props { events: FeedEvent[]; }

const KIND_TONE: Record<string, "accent" | "amber" | "indigo" | "danger" | "muted"> = {
  user_message: "indigo" as const,
  assistant_reply: "accent" as const,
  tool_call: "accent" as const,
  tool_result: "muted",
  llm_call: "amber" as const,
} as Record<string, "accent" | "amber" | "indigo" | "danger" | "muted">;

const KIND_LABEL: Record<string, string> = {
  user_message: "you",
  assistant_reply: "reply",
  tool_call: "tool",
  tool_result: "result",
  llm_call: "model",
};

export function RecentActivity({ events }: Props) {
  const recent = [...events].slice(-10).reverse();

  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Recent activity
        </div>
        <Link to="/traces" className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-accent)] transition-colors">
          full trace →
        </Link>
      </div>
      <Card className="overflow-hidden p-0">
        {recent.length === 0 ? (
          <div className="px-4 py-5 text-[12.5px] text-[var(--color-text-muted)]">
            No activity yet.
          </div>
        ) : recent.map((e, i) => (
          <div
            key={`${e.ts}-${i}`}
            className="grid grid-cols-[64px_64px_1fr] gap-3 items-center px-4 h-9"
            style={{ borderBottom: "1px solid var(--color-border)" }}
          >
            <span
              className="text-[11px] tabular"
              style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
            >
              {formatTime(e.ts)}
            </span>
            <span><MiniBadge tone={KIND_TONE[e.event] ?? "muted"}>{KIND_LABEL[e.event] ?? e.event}</MiniBadge></span>
            <span className="text-[12px] text-[var(--color-text-dim)] truncate">
              {detail(e)}
            </span>
          </div>
        ))}
      </Card>
    </div>
  );
}

function MiniBadge({ tone, children }: { tone: string; children: React.ReactNode }) {
  const tones: Record<string, { bg: string; fg: string }> = {
    accent: { bg: "var(--color-accent-dim)", fg: "var(--color-accent)" },
    amber:  { bg: "var(--color-amber-dim)",  fg: "var(--color-amber)" },
    indigo: { bg: "var(--color-indigo-dim)", fg: "var(--color-indigo)" },
    muted:  { bg: "var(--color-surface-3)",  fg: "var(--color-text-muted)" },
  };
  const c = tones[tone] ?? tones.muted;
  return (
    <span
      className="inline-flex items-center px-1.5 h-4 text-[10px] font-medium rounded-[3px]"
      style={{ background: c.bg, color: c.fg, letterSpacing: 0.02 }}
    >
      {children}
    </span>
  );
}
// Inline so we don't have to import a heavier Badge here.
const _Badge = Badge;
void _Badge;

function detail(e: FeedEvent): string {
  switch (e.event) {
    case "user_message":    return e.text ?? "";
    case "assistant_reply": return e.text ?? "";
    case "tool_call":       return `${e.name ?? ""} ${e.args ? `· ${e.args.slice(0, 80)}` : ""}`;
    case "tool_result":     return e.result ?? "";
    case "llm_call":        return `${e.model ?? ""}`;
    default:                return "";
  }
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}
