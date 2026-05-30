import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";

interface Props {
  task: Task;
  onChanged: (next: Task) => void;
  onDeleted: (id: string) => void;
  onOpenDetail: () => void;
}

export function TaskRow({ task, onChanged, onDeleted, onOpenDetail }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!task.due_at || task.status !== "active") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [task.due_at, task.status]);

  const stop = (e: React.MouseEvent) => e.stopPropagation();
  const runNow = async (e: React.MouseEvent) => {
    stop(e); setBusy("run");
    try { onChanged(await api.tasksRunNow(task.id)); } finally { setBusy(null); }
  };
  const cancel = async (e: React.MouseEvent) => {
    stop(e);
    if (!confirm(`CANCEL "${task.title}"?`)) return;
    setBusy("cancel");
    try { onChanged(await api.tasksCancel(task.id)); } finally { setBusy(null); }
  };
  const remove = async (e: React.MouseEvent) => {
    stop(e);
    if (!confirm(`PERMANENTLY DELETE "${task.title}"?`)) return;
    setBusy("del");
    try { await api.tasksDelete(task.id); onDeleted(task.id); } catch { setBusy(null); }
  };

  const isActive = task.status === "active";
  const due = task.due_at ? new Date(task.due_at).getTime() : null;
  const isOverdue = due !== null && due <= now && isActive;
  const lastRunFailed = task.last_runs?.length > 0 && task.last_runs[task.last_runs.length - 1]?.status === "failure";

  const badge = getBadge(task, isOverdue, lastRunFailed);
  const icon = lastRunFailed ? "▴" : "◷";
  const iconColor = lastRunFailed
    ? "var(--color-amber)"
    : isOverdue
      ? "var(--color-danger)"
      : task.status === "active"
        ? "var(--color-accent)"
        : "var(--color-text-faint)";

  const subtitle = buildSubtitle(task, due, now, isOverdue);

  return (
    <div
      onClick={onOpenDetail}
      className="group cursor-pointer"
      style={{
        borderBottom: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
        background: "transparent",
        transition: "background 0.12s",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(124,254,0,0.03)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
    >
      <div
        className="grid items-center gap-5 px-4 py-3"
        style={{ gridTemplateColumns: "20px 1fr auto" }}
      >
        {/* Icon */}
        <span style={{ fontSize: 13, color: iconColor, lineHeight: 1 }}>{icon}</span>

        {/* Title + subtitle */}
        <div>
          <div
            style={{
              fontSize: 13,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: task.status === "active" ? "var(--color-text)" : "var(--color-text-muted)",
            }}
          >
            {task.title}
          </div>
          {subtitle && (
            <div style={{ fontSize: 10, letterSpacing: "0.10em", textTransform: "uppercase", color: "var(--color-text-muted)", marginTop: 3 }}>
              {subtitle}
            </div>
          )}
        </div>

        {/* Badge */}
        <div className="flex items-center gap-3 shrink-0">
          <span
            style={{
              fontSize: 10,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: badge.color,
              fontWeight: 600,
            }}
          >
            {badge.label}
          </span>
        </div>
      </div>

      {/* Hover actions */}
      <div className="px-4 pb-2 flex items-center gap-4 text-[10px] uppercase tracking-[0.14em] opacity-0 group-hover:opacity-100 transition-opacity">
        {isActive && (
          <>
            <Action onClick={runNow} color="var(--color-accent)" busy={busy === "run"}>run now</Action>
            <Action onClick={cancel} color="var(--color-text-muted)" busy={busy === "cancel"}>cancel</Action>
          </>
        )}
        <Action onClick={remove} color="var(--color-danger)" busy={busy === "del"}>delete</Action>
      </div>
    </div>
  );
}

function getBadge(task: Task, isOverdue: boolean, lastRunFailed: boolean): { label: string; color: string } {
  if (task.status === "completed") return { label: "DONE", color: "var(--color-text-muted)" };
  if (task.status === "cancelled") return { label: "CANCELLED", color: "var(--color-text-faint)" };
  if (lastRunFailed) return { label: "RETRY", color: "var(--color-amber)" };
  if (isOverdue) return { label: "OVERDUE", color: "var(--color-danger)" };
  return { label: "ARMED", color: "var(--color-accent)" };
}

function buildSubtitle(task: Task, dueMs: number | null, nowMs: number, isOverdue: boolean): string {
  const parts: string[] = [];
  if (task.recurrence !== "none") parts.push(task.recurrence);

  if (dueMs !== null) {
    const diff = Math.abs(dueMs - nowMs);
    const s = Math.floor(diff / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const timeStr = h > 0 ? `${h}h ${m}m` : `${m}m`;
    parts.push(isOverdue ? `${timeStr} ago` : `next in ${timeStr}`);
  }

  const runs = task.last_runs || [];
  if (runs.length > 0) {
    const ok = runs.filter((r) => r.status === "success").length;
    parts.push(`${ok}/${runs.length} ok`);
  }

  return parts.join(" · ");
}

function Action({
  onClick, color, busy, children,
}: {
  onClick: (e: React.MouseEvent) => void;
  color: string; busy?: boolean; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="px-2 py-0.5 transition-colors disabled:opacity-50"
      style={{ background: "transparent", color, border: `1px solid ${color}`, fontFamily: "var(--font-mono)" }}
      onMouseEnter={(e) => { if (busy) return; const el = e.currentTarget as HTMLButtonElement; el.style.background = color; el.style.color = "var(--color-bg)"; }}
      onMouseLeave={(e) => { if (busy) return; const el = e.currentTarget as HTMLButtonElement; el.style.background = "transparent"; el.style.color = color; }}
    >
      [{busy ? "…" : children}]
    </button>
  );
}
