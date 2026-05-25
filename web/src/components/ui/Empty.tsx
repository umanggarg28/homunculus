import type { ReactNode } from "react";

/** Brutalist empty state — hairline box, terminal voice. */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      className="p-8 text-center text-[11px] uppercase tracking-[0.16em]"
      style={{
        background: "transparent",
        border: "1px solid var(--color-border)",
        color: "var(--color-text-muted)",
        fontFamily: "var(--font-mono)",
      }}
    >
      ─ {children} ─
    </div>
  );
}
