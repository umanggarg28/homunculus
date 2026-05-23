import type { ReactNode } from "react";
import { motion } from "framer-motion";

interface Props {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: "mint" | "amber" | "indigo" | "muted";
}

const accentColors: Record<NonNullable<Props["accent"]>, string> = {
  mint:   "var(--color-accent)",
  amber:  "var(--color-amber)",
  indigo: "var(--color-indigo)",
  muted:  "var(--color-text-muted)",
};

export function StatTile({ label, value, hint, accent = "mint" }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-[10px] p-4 relative overflow-hidden"
      style={{
        background: "var(--color-surface-2)",
        border: "1px solid var(--color-border)",
      }}
    >
      {/* Subtle accent strip on the left edge */}
      <div
        className="absolute left-0 top-0 bottom-0 w-[2px]"
        style={{ background: accentColors[accent], opacity: 0.55 }}
      />
      <div className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </div>
      <div
        className="readout mt-1.5 text-[var(--color-text)]"
        style={{ fontSize: 26, lineHeight: 1.05 }}
      >
        {value}
      </div>
      {hint && (
        <div className="text-[11.5px] mt-1.5" style={{ color: "var(--color-text-muted)" }}>
          {hint}
        </div>
      )}
    </motion.div>
  );
}
