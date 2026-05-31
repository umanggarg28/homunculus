import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, parseServerIso } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import type { Task } from "@/lib/types";

export function UpNextList() {
  const [tasks, setTasks] = useState<Task[] | null>(null);

  useEffect(() => {
    api.tasksList("active").then(setTasks).catch(() => setTasks([]));
  }, []);

  const upcoming = (tasks ?? [])
    .filter((t) => t.due_at)
    .sort((a, b) => (a.due_at! < b.due_at! ? -1 : 1))
    .slice(0, 4);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Up next
        </div>
        <Link to="/tasks" className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-accent)] transition-colors">
          all →
        </Link>
      </div>
      <Card className="overflow-hidden p-0">
        {tasks === null ? null : upcoming.length === 0 ? (
          <div className="px-4 py-5 text-[12.5px] text-[var(--color-text-muted)]">
            Nothing scheduled. The agent is at rest.
          </div>
        ) : upcoming.map((t) => (
          <Link
            key={t.id}
            to="/tasks"
            className="flex items-center gap-3 px-4 h-11 hover:bg-[var(--color-surface-3)] transition-colors"
            style={{ borderBottom: "1px solid var(--color-border)" }}
          >
            <span className="flex-1 min-w-0 text-[13px] text-[var(--color-text)] truncate">
              {t.title}
            </span>
            <Badge tone={t.recurrence === "daily" ? "success" : t.recurrence === "weekly" ? "warning" : "muted"}>
              {t.recurrence === "none" ? "one-shot" : t.recurrence}
            </Badge>
            <span
              className="text-[11px] tabular shrink-0 w-[80px] text-right"
              style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
            >
              {formatDue(t.due_at!)}
            </span>
          </Link>
        ))}
      </Card>
    </div>
  );
}

function formatDue(iso: string): string {
  const diffMin = Math.floor((parseServerIso(iso) - Date.now()) / 60000);
  if (diffMin < 0) return "overdue";
  if (diffMin < 60) return `in ${diffMin}m`;
  if (diffMin < 60 * 24) return `in ${Math.floor(diffMin / 60)}h`;
  return `${Math.floor(diffMin / (60 * 24))}d`;
}
