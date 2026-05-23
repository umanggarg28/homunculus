import type { ReactNode } from "react";

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      className="rounded-[8px] p-10 text-center"
      style={{
        background: "var(--color-surface-2)",
        border: "1px dashed var(--color-border-strong)",
        color: "var(--color-text-muted)",
        fontSize: 13.5,
      }}
    >
      {children}
    </div>
  );
}
