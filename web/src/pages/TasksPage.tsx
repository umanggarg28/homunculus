import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Empty } from "@/components/ui/Empty";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TaskRow } from "@/components/tasks/TaskRow";
import { TaskDetailDrawer } from "@/components/tasks/TaskDetailDrawer";
import { TaskForm } from "@/components/tasks/TaskForm";
import type { Task } from "@/lib/types";

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [detailFor, setDetailFor] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.tasksList("all").then(setTasks).catch(() => setTasks([]));
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
    <div className="max-w-[1000px] mx-auto px-8 pt-10 pb-16">
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

      {tasks === null ? null : tasks.length === 0 ? (
        <Empty>No tasks yet. Tasks define recurring or scheduled work the agent fires.</Empty>
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
