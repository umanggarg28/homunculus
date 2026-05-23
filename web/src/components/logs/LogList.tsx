import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import type { LogFile } from "@/lib/types";

export function LogList({ logs }: { logs: LogFile[] }) {
  return (
    <Card className="overflow-hidden p-0">
      {logs.map((lf) => (
        <Link
          key={lf.rel}
          to={`/logs/${lf.rel}`}
          className="group flex items-center justify-between px-4 h-11 hover:bg-[var(--color-surface-3)] transition-colors"
          style={{ borderBottom: "1px solid var(--color-border)" }}
        >
          <span
            className="text-[13.5px] font-medium text-[var(--color-text)] group-hover:text-[var(--color-accent)] tabular"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {lf.date}
          </span>
          <span className="text-[11.5px] text-[var(--color-text-muted)] tabular">
            {lf.size_kb} kb
          </span>
        </Link>
      ))}
    </Card>
  );
}
