import { Link } from "react-router-dom";
import { useState, type MouseEvent } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { MemoryEntry } from "@/lib/types";

const TYPE_TONE: Record<string, "accent" | "amber" | "indigo" | "muted" | "default" | "success"> = {
  user: "indigo",
  feedback: "amber" as const,
  project: "default",
  reference: "muted",
  skill: "accent",
} as Record<string, "accent" | "amber" | "indigo" | "muted" | "default" | "success">;

interface Props {
  entry: MemoryEntry;
  onDeleted: (filename: string) => void;
}

export function MemoryCard({ entry, onDeleted }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const startDelete = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); setConfirming(true); };
  const cancel = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); setConfirming(false); };
  const confirm = async (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true);
    try { await api.memoryDelete(entry.filename); onDeleted(entry.filename); }
    catch { setBusy(false); setConfirming(false); }
  };

  return (
    <div
      className="group flex items-start gap-4 px-4 py-3 hover:bg-[var(--color-surface-3)] transition-colors"
      style={{ borderBottom: "1px solid var(--color-border)" }}
    >
      <Link to={`/memory/${entry.filename}`} className="flex-1 min-w-0 flex items-start gap-3">
        <div className="shrink-0 mt-0.5">
          <Badge tone={(TYPE_TONE[entry.type] ?? "muted") as "accent" | "amber" | "indigo" | "muted" | "default" | "success"}>
            {entry.type}
          </Badge>
        </div>
        <div className="min-w-0">
          <div className="text-[13.5px] font-medium text-[var(--color-text)] truncate group-hover:text-[var(--color-accent)] transition-colors">
            {entry.name}
          </div>
          <div className="mt-0.5 text-[12.5px] text-[var(--color-text-muted)] line-clamp-1">
            {entry.description}
          </div>
        </div>
      </Link>

      <div className="shrink-0 self-center">
        {!confirming ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={startDelete}
            className="opacity-0 group-hover:opacity-100 transition-opacity"
          >
            Delete
          </Button>
        ) : (
          <div className="flex gap-1">
            <Button size="sm" variant="danger" onClick={confirm} disabled={busy}>
              {busy ? "…" : "Confirm"}
            </Button>
            <Button size="sm" variant="ghost" onClick={cancel}>Cancel</Button>
          </div>
        )}
      </div>
    </div>
  );
}
