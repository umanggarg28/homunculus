import type { ReactNode } from "react";

type Tone = "default" | "accent" | "amber" | "indigo" | "success" | "warning" | "danger" | "muted";

const tones: Record<Tone, { bg: string; fg: string; border: string }> = {
  default: { bg: "var(--color-surface-3)",   fg: "var(--color-text-dim)",  border: "var(--color-border)" },
  accent:  { bg: "var(--color-accent-dim)",  fg: "var(--color-accent)",    border: "rgba(94,234,212,0.32)" },
  amber:   { bg: "var(--color-amber-dim)",   fg: "var(--color-amber)",     border: "rgba(245,158,11,0.32)" },
  indigo:  { bg: "var(--color-indigo-dim)",  fg: "var(--color-indigo)",    border: "rgba(129,140,248,0.32)" },
  success: { bg: "rgba(74,222,128,0.12)",    fg: "var(--color-success)",   border: "rgba(74,222,128,0.32)" },
  warning: { bg: "rgba(245,158,11,0.12)",    fg: "var(--color-warning)",   border: "rgba(245,158,11,0.32)" },
  danger:  { bg: "rgba(248,113,113,0.12)",   fg: "var(--color-danger)",    border: "rgba(248,113,113,0.32)" },
  muted:   { bg: "transparent",              fg: "var(--color-text-muted)", border: "var(--color-border)" },
};

export function Badge({
  children, tone = "default",
}: { children: ReactNode; tone?: Tone }) {
  const c = tones[tone];
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 h-5 text-[11px] font-medium rounded-[4px] border"
      style={{ background: c.bg, color: c.fg, borderColor: c.border, letterSpacing: 0.02 }}
    >
      {children}
    </span>
  );
}
