import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TasksHero } from "@/components/ui/HeroBand";
import { TaskRow } from "@/components/tasks/TaskRow";
import { TaskDetailDrawer } from "@/components/tasks/TaskDetailDrawer";
import { TaskForm } from "@/components/tasks/TaskForm";
import type { Task } from "@/lib/types";

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [detailFor, setDetailFor] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    api.tasksList("all").then(setTasks).catch(() => setTasks([]));
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const { active, completed, cancelled } = useMemo(() => {
    const a: Task[] = [], c: Task[] = [], x: Task[] = [];
    for (const t of tasks ?? []) {
      if (t.status === "active") a.push(t);
      else if (t.status === "completed") c.push(t);
      else if (t.status === "cancelled") x.push(t);
    }
    return { active: a, completed: c, cancelled: x };
  }, [tasks]);

  const onChanged = (next: Task) =>
    setTasks((cur) => (cur ?? []).map((t) => (t.id === next.id ? next : t)));
  const onDeleted = (id: string) =>
    setTasks((cur) => (cur ?? []).filter((t) => t.id !== id));
  const onCreated = (t: Task) => setTasks((cur) => [...(cur ?? []), t]);

  return (
    <PageShell>
      <PageHeader
        title="Tasks"
        subtitle={
          tasks
            ? `${active.length} active · ${completed.length} completed · ${cancelled.length} cancelled`
            : ""
        }
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            + New task
          </Button>
        }
      />

      {tasks && tasks.length > 0 && <TasksHero tasks={tasks} now={now} />}

      {tasks === null ? null : tasks.length === 0 ? (
        <BrutalistEmptyTasks onCreate={() => setCreating(true)} />
      ) : (
        <div className="flex flex-col gap-8">
          {active.length > 0 && (
            <Group label="Active" count={active.length}>
              {active.map((t) => (
                <TaskRow key={t.id} task={t} onChanged={onChanged} onDeleted={onDeleted} onOpenDetail={() => setDetailFor(t)} />
              ))}
            </Group>
          )}
          {completed.length > 0 && (
            <Group label="Completed" count={completed.length}>
              {completed.map((t) => (
                <TaskRow key={t.id} task={t} onChanged={onChanged} onDeleted={onDeleted} onOpenDetail={() => setDetailFor(t)} />
              ))}
            </Group>
          )}
          {cancelled.length > 0 && (
            <Group label="Cancelled" count={cancelled.length}>
              {cancelled.map((t) => (
                <TaskRow key={t.id} task={t} onChanged={onChanged} onDeleted={onDeleted} onOpenDetail={() => setDetailFor(t)} />
              ))}
            </Group>
          )}
        </div>
      )}

      <TaskDetailDrawer task={detailFor} onClose={() => setDetailFor(null)} />
      <TaskForm open={creating} onClose={() => setCreating(false)} onCreated={onCreated} />
    </PageShell>
  );
}

function BrutalistEmptyTasks({ onCreate }: { onCreate: () => void }) {
  const SAMPLES = [
    "ping me at 9am about today's leetcode",
    "every monday morning, send me what's pending in the repo",
    "remind me to backup memory.md once a week",
    "fetch the mcp spec changelog every friday at 5pm",
  ];
  return (
    <div
      className="p-8 mt-2"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <div
        className="text-[10px] uppercase tracking-[0.22em] mb-4"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── no tasks queued
      </div>
      <div className="text-[13px] leading-[1.7] mb-6" style={{ color: "var(--color-text-dim)" }}>
        the agent's autonomous queue is empty. tasks are how it remembers to
        do things — recurring deliveries, one-shot reminders, scheduled work
        that fires on its own.
      </div>

      <div
        className="text-[10px] uppercase tracking-[0.18em] mb-3"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── try asking it
      </div>
      <div className="mb-6">
        {SAMPLES.map((s, i) => (
          <div
            key={i}
            className="py-1 text-[12.5px]"
            style={{ color: "var(--color-text-dim)" }}
          >
            <span style={{ color: "var(--color-accent)", marginRight: 8 }}>▸</span>
            "{s}"
          </div>
        ))}
      </div>

      <button
        onClick={onCreate}
        className="text-[11px] uppercase tracking-[0.16em] px-3 py-2 transition-colors"
        style={{
          background: "transparent",
          color: "var(--color-accent)",
          border: "1px solid var(--color-accent)",
        }}
        onMouseEnter={(e) => {
          const el = e.currentTarget as HTMLButtonElement;
          el.style.background = "var(--color-accent)";
          el.style.color = "var(--color-bg)";
        }}
        onMouseLeave={(e) => {
          const el = e.currentTarget as HTMLButtonElement;
          el.style.background = "transparent";
          el.style.color = "var(--color-accent)";
        }}
      >
        [+ new task]
      </button>
    </div>
  );
}

function Group({ label, count, children }: { label: string; count: number; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2 px-1">
        <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          {label}
        </span>
        <span className="text-[11px] text-[var(--color-text-faint)]">{count}</span>
      </div>
      <Card className="overflow-hidden p-0">
        {children}
      </Card>
    </div>
  );
}
