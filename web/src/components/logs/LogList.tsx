import { Link } from "react-router-dom";
import type { LogFile } from "@/lib/types";

/** Brutalist log list — terminal-style `YYYY-MM-DD · NN.N kb` rows.
 *  Each row is a directory-listing-style line, hairline border between.
 */
export function LogList({ logs }: { logs: LogFile[] }) {
  return (
    <div style={{ borderTop: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}>
      {logs.map((lf) => (
        <Link
          key={lf.rel}
          to={`/logs/${lf.rel}`}
          className="group block transition-colors"
          style={{ borderBottom: "1px solid var(--color-border)" }}
        >
          <div
            className="px-4 py-2 grid items-baseline gap-5"
            style={{ gridTemplateColumns: "auto 1fr auto auto" }}
            onMouseEnter={(e) => {
              (e.currentTarget.parentElement as HTMLAnchorElement).style.background = "var(--color-surface-2)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget.parentElement as HTMLAnchorElement).style.background = "transparent";
            }}
          >
            <span className="brut-label shrink-0" style={{ color: "var(--color-text-muted)" }}>
              [log]
            </span>
            <span className="brut-body brut-num" style={{ color: "var(--color-text)" }}>
              {lf.date}
            </span>
            <span className="brut-label brut-num" style={{ color: "var(--color-text-faint)" }}>
              {lf.size_kb} kb
            </span>
            <span
              className="brut-label opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ color: "var(--color-accent)" }}
            >
              [open ↗]
            </span>
          </div>
        </Link>
      ))}
    </div>
  );
}
