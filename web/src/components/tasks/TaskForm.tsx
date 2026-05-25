import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Textarea } from "@/components/ui/Input";
import type { Recurrence, Task } from "@/lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (task: Task) => void;
}

const RECURRENCE_OPTIONS: Recurrence[] = ["none", "daily", "weekly"];

export function TaskForm({ open, onClose, onCreated }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [recurrence, setRecurrence] = useState<Recurrence>("none");
  const [notify, setNotify] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setTitle(""); setDescription(""); setDueAt("");
      setRecurrence("none"); setNotify(false); setErr(null);
    }
  }, [open]);

  const submit = async () => {
    if (!title.trim()) { setErr("Title is required."); return; }
    setBusy(true); setErr(null);
    try {
      const task = await api.tasksCreate({
        title: title.trim(),
        description: description.trim(),
        due_at: dueAt ? `${dueAt}:00` : null,
        recurrence,
        notify,
      });
      onCreated(task);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New task"
      description="Tasks define recurring or scheduled work the heartbeat will fire."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy}>
            {busy ? "Creating…" : "Create task"}
          </Button>
        </>
      }
    >
      <Field label="Title">
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
          placeholder="e.g. Daily journal prompt"
        />
      </Field>

      <Field label="Description" hint="What should the agent actually do when this fires?">
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
        />
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field label="First due">
          <Input
            type="datetime-local"
            value={dueAt}
            onChange={(e) => setDueAt(e.target.value)}
          />
        </Field>
        <Field label="Recurrence">
          <Select
            value={recurrence}
            onChange={(e) => setRecurrence(e.target.value as Recurrence)}
          >
            {RECURRENCE_OPTIONS.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </Select>
        </Field>
      </div>

      <label className="flex items-center gap-2 mt-2 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={notify}
          onChange={(e) => setNotify(e.target.checked)}
          className="accent-[var(--color-accent)]"
        />
        <span className="text-[13.5px] text-[var(--color-text-dim)]">
          Notify via Telegram when this fires
        </span>
      </label>

      {err && (
        <div className="mt-3 text-[12.5px] text-[var(--color-danger)]">{err}</div>
      )}
    </Modal>
  );
}
