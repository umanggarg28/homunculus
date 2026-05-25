import { Link } from "react-router-dom";

/** Brutalist back link — `[ ← LABEL ]` mono uppercase, inverts on hover. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="inline-block mb-4 text-[10px] uppercase tracking-[0.16em] px-2 py-1 transition-colors"
      style={{
        color: "var(--color-text-muted)",
        border: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLAnchorElement;
        el.style.color = "var(--color-accent)";
        el.style.borderColor = "var(--color-accent)";
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLAnchorElement;
        el.style.color = "var(--color-text-muted)";
        el.style.borderColor = "var(--color-border)";
      }}
    >
      [← {label}]
    </Link>
  );
}
