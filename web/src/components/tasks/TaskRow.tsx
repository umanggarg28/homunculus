import { useEffect, useState } from "react";
import { api, parseTaskWallClock } from "@/lib/api";
import type { Task } from "@/lib/types";
import { DataTip } from "@/components/ui/DataTip";
import { Tooltip } from "@/components/ui/Tooltip";
import { RunNowPanel } from "./RunNowPanel";

interface Props {
  task: Task;
  onChanged: (next: Task) => void;
  onDeleted: (id: string) => void;
  onOpenDetail: () => void;
}

export function TaskRow({ task, onChanged, onDeleted, onOpenDetail }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [runOpen, setRunOpen] = useState(false);

  useEffect(() => {
    if (!task.due_at || task.status !== "active") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [task.due_at, task.status]);

  const stop = (e: React.MouseEvent) => e.stopPropagation();
  // T1.4 of the capability roadmap: click [run now] opens a side drawer
  // that streams the agent's execution live. The old API endpoint
  // (POST /api/tasks/{id}/run-now) which just bumps due_at is still
  // available — invoke it explicitly when the user only wants to
  // schedule, but the default UX is now the streaming panel.
  const runNow = (e: React.MouseEvent) => {
    stop(e);
    setRunOpen(true);
  };
  const closeRun = () => {
    setRunOpen(false);
    // Refresh the task data once after a run so the row's badge /
    // sparkline / due_at reflect any state changes (success advances
    // due_at, failure shows up in last_runs).
    api.tasksGet(task.id).then(onChanged).catch(() => undefined);
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
  const due = task.due_at ? parseTaskWallClock(task.due_at) : null;
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
    <>
    {/* Live run-stream drawer — sibling to the row so it can size to viewport. */}
    <RunNowPanel
      taskId={task.id}
      taskTitle={task.title}
      open={runOpen}
      onClose={closeRun}
    />
    <div
      onClick={onOpenDetail}
      className="task-row hm-interactive-row group cursor-pointer"
      style={{
        borderBottom: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
      }}
      // Hover bloom is owned by .hm-interactive-row (atmosphere.css) —
      // the inline onMouseEnter handlers were a per-component fallback
      // when the utility CSS was trimmed; revived now, drop the inline.
    >
      <style>{`
        .task-row-grid {
          display: grid;
          grid-template-columns: 20px minmax(0, 1fr) auto;
        }
        .task-row-meta {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 4px;
        }
        .task-row-actions {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        @media (max-width: 640px) {
          .task-row-grid {
            grid-template-columns: 16px minmax(0, 1fr);
          }
          .task-row-badge {
            grid-column: 2;
            justify-content: flex-start;
            margin-top: 8px;
          }
          .task-row-meta {
            align-items: flex-start;
            flex-direction: column;
            gap: 6px;
          }
          .task-row-actions {
            flex-wrap: wrap;
            gap: 8px;
            opacity: 1 !important;
          }
        }
      `}</style>
      <div
        className="task-row-grid items-center gap-5 px-4 py-3"
      >
        {/* Icon */}
        <span style={{ fontSize: 13, color: iconColor, lineHeight: 1 }}>{icon}</span>

        {/* Title + subtitle + run sparkline */}
        <div>
          <div
            style={{
              fontSize: 13,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: task.status === "active" ? "var(--color-text)" : "var(--color-text-muted)",
              // Cancelled tasks read identically to completed ones at a glance;
              // strikethrough is the editorial cue without introducing color.
              textDecoration: task.status === "cancelled" ? "line-through" : "none",
              textDecorationColor: "var(--color-text-faint)",
            }}
          >
            {task.title}
          </div>
          <div className="task-row-meta">
            {task.source === "inferred" && (
              <Tooltip text="A commitment the agent noticed and recorded to follow up on — you didn't ask for it.">
              <span
                style={{
                  fontSize: 9,
                  letterSpacing: "0.14em",
                  textTransform: "uppercase",
                  color: "var(--color-indigo)",
                  border: "1px solid var(--color-indigo)",
                  borderRadius: 2,
                  padding: "1px 5px",
                  lineHeight: 1.3,
                  whiteSpace: "nowrap",
                }}
              >
                ◇ proactive
              </span>
              </Tooltip>
            )}
            {subtitle && (
              <div style={{ fontSize: 10, letterSpacing: "0.10em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                {subtitle}
              </div>
            )}
            {task.last_runs?.length > 0 && (
              <RunSparkline runs={task.last_runs} />
            )}
          </div>
        </div>

        {/* Badge */}
        <div className="task-row-badge flex items-center gap-3 shrink-0">
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
      <div className="task-row-actions px-4 pb-2 text-[10px] uppercase tracking-[0.14em] opacity-0 group-hover:opacity-100 transition-opacity">
        {isActive && (
          <>
            <Action onClick={runNow} color="var(--color-accent)" busy={busy === "run"}>run now</Action>
            <Action onClick={cancel} color="var(--color-text-muted)" busy={busy === "cancel"}>cancel</Action>
          </>
        )}
        <Action onClick={remove} color="var(--color-danger)" busy={busy === "del"}>delete</Action>
      </div>
    </div>
    </>
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

  // A finished one-shot has no next fire — "next in 219h" on a DONE row
  // was the stale due_at counting toward nothing. Show when it closed.
  if (task.status !== "active") {
    const closedIso = task.completed_at || task.updated_at;
    if (closedIso) {
      const closed = new Date(closedIso);
      parts.push(
        `${task.status === "cancelled" ? "cancelled" : "done"} ` +
        closed.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }),
      );
    }
    return parts.join(" · ");
  }

  if (dueMs !== null) {
    const diff = Math.abs(dueMs - nowMs);
    const s = Math.floor(diff / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const timeStr = h > 0 ? `${h}h ${m}m` : `${m}m`;
    parts.push(isOverdue ? `${timeStr} ago` : `next in ${timeStr}`);
  }

  return parts.join(" · ");
}

function RunSparkline({ runs }: { runs: import("@/lib/types").TaskRun[] }) {
  const last = runs.slice(-12);
  // "ok" means the run actually delivered (status success) — a partial is a
  // run where the user received nothing (silent drop / infra retry), so
  // counting it as ok would overstate reliability.
  const okCount = last.filter((r) => r.status === "success").length;
  const label = `${okCount}/${last.length} ok`;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
      {last.map((r, i) => {
        const d = new Date(parseTaskWallClock(r.ts));
        const tipText = [
          d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }),
          r.status,
          r.duration_s != null ? `${r.duration_s}s` : null,
        ].filter(Boolean).join(" · ");

        return (
          <DataTip key={i} tip={tipText}>
            <div
              style={{
                width: 6,
                height: 12,
                background: r.status === "success" ? "var(--color-accent)" : "var(--color-danger)",
                opacity: 0.3 + 0.7 * ((i + 1) / last.length),
                cursor: "default",
              }}
            />
          </DataTip>
        );
      })}
      <span style={{ fontSize: 9, letterSpacing: "0.08em", color: okCount < last.length ? "var(--color-amber)" : "var(--color-text-faint)", marginLeft: 2 }}>
        {label}
      </span>
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
      className="px-2 py-0.5 transition-colors disabled:opacity-50"
      style={{ background: "transparent", color, border: `1px solid ${color}`, fontFamily: "var(--font-mono)" }}
      onMouseEnter={(e) => { if (busy) return; const el = e.currentTarget as HTMLButtonElement; el.style.background = color; el.style.color = "var(--color-bg)"; }}
      onMouseLeave={(e) => { if (busy) return; const el = e.currentTarget as HTMLButtonElement; el.style.background = "transparent"; el.style.color = color; }}
    >
      [{busy ? "…" : children}]
    </button>
  );
}
