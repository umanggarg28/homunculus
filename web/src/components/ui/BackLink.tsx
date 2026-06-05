import { Link } from "react-router-dom";
import { Tooltip } from "@/components/ui/Tooltip";

/** Brutalist back link — `[ ← LABEL ]` mono uppercase, inverts on hover. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Tooltip text={<>Go back to <strong>{label}</strong> ({to})</>} placement="bottom">
      <Link
        to={to}
        className="hm-bracket-link inline-block mb-4 text-[10px] uppercase tracking-[0.16em] px-2 py-1"
        style={{
          fontFamily: "var(--font-mono)",
        }}
      >
        [← {label}]
      </Link>
    </Tooltip>
  );
}
