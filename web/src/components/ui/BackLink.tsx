import { Link } from "react-router-dom";

export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1.5 mb-4 text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
    >
      <span>←</span>
      <span>{label}</span>
    </Link>
  );
}
