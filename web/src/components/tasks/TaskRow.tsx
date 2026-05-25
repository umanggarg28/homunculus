import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";

interface Props {
  task: Task;
  onChanged: (next: Task) => void;
  onDeleted: (id: string) => void;
  onOpenDetail: () => void;
}

/** Brutalist task row — hairline border between, mono everywhere,
 *  status pill is text not chip, live countdown next to title, inline
 *  bracketed actions on hover. */
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
  const dueLabel = due ? formatDueLabel(due, now) : null;
  const isOverdue = due !== null && due <= now && isActive;
  const successRate = computeSuccessRate(task);

  return (
    <div
      onClick={onOpenDetail}
      className="group cursor-pointer transition-colors py-[10px] px-4"
      style={{
        borderBottom: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
        background: "transparent",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--color-surface-2)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
    >
      <div className="grid items-baseline gap-5" style={{ gridTemplateColumns: "1fr auto" }}>
        {/* Left: status dot + title + recurrence */}
        <div className="min-w-0 flex items-baseline gap-3">
          <span
            className="brut-label shrink-0"
            style={{
              color:
                task.status === "active"
                  ? isOverdue
                    ? "var(--color-danger)"
                    : "var(--color-accent)"
                  : task.status === "completed"
                    ? "var(--color-text-muted)"
                    : "var(--color-text-faint)",
            }}
          >
            ●
          </span>
          <span className="brut-body truncate" style={{ color: "var(--color-text)" }}>
            {task.title}
          </span>
          <span className="brut-label shrink-0" style={{ color: cadenceColor(task.recurrence) }}>
            [{task.recurrence === "none" ? "one-shot" : task.recurrence}]
          </span>
          {task.notify && (
            <span className="brut-label shrink-0" style={{ color: "var(--color-amber)" }}>
              [notify]
            </span>
          )}
        </div>

        {/* Right: countdown + success rate (always visible) */}
        <div className="flex items-baseline gap-5 brut-label shrink-0">
          {dueLabel && (
            <span
              style={{
                color: isOverdue ? "var(--color-danger)" : "var(--color-text-dim)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {dueLabel}
            </span>
          )}
          {successRate && (
            <span style={{ color: "var(--color-text-muted)" }}>
              {successRate}
            </span>
          )}
        </div>
      </div>

      {/* Hover actions row */}
      <div className="mt-2 flex items-center gap-4 text-[10px] uppercase tracking-[0.14em] opacity-0 group-hover:opacity-100 transition-opacity">
        {isActive && (
          <>
            <Action onClick={runNow} color="var(--color-accent)" busy={busy === "run"}>run now</Action>
            <Action onClick={cancel} color="var(--color-text-muted)" busy={busy === "cancel"}>cancel</Action>
          </>
        )}
        <Action onClick={remove} color="var(--color-danger)" busy={busy === "del"}>delete</Action>
        <span style={{ color: "var(--color-border-strong)" }}>──</span>
        <span style={{ color: "var(--color-text-faint)" }}>click row for detail</span>
      </div>
    </div>
  );
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
      className="px-2 py-1 transition-colors disabled:opacity-50"
      style={{
        background: "transparent",
        color,
        border: `1px solid ${color}`,
        fontFamily: "var(--font-mono)",
      }}
      onMouseEnter={(e) => {
        if (busy) return;
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = color;
        el.style.color = "var(--color-bg)";
      }}
      onMouseLeave={(e) => {
        if (busy) return;
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = "transparent";
        el.style.color = color;
      }}
    >
      [{busy ? "…" : children}]
    </button>
  );
}

function cadenceColor(r: string): string {
  if (r === "daily")  return "var(--color-accent)";
  if (r === "weekly") return "var(--color-amber)";
  return "var(--color-text-faint)";
}

function computeSuccessRate(t: Task): string | null {
  const runs = t.last_runs || [];
  if (runs.length === 0) return null;
  const ok = runs.filter((r) => r.status === "success").length;
  return `${ok}/${runs.length} ok`;
}

function formatDueLabel(dueMs: number, nowMs: number): string {
  const diffMs = dueMs - nowMs;
  const past = diffMs < 0;
  const absMs = Math.abs(diffMs);
  const s = Math.floor(absMs / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const fmt =
    h > 0  ? `${pad(h)}:${pad(m)}:${pad(sec)}`
      : `${pad(m)}:${pad(sec)}`;
  return past ? `−${fmt} overdue` : `t−${fmt}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}
