import { useMemo, useState } from "react";
import { MemoryCard } from "./MemoryCard";
import { Card } from "@/components/ui/Card";
import type { MemoryEntry } from "@/lib/types";

const TYPE_ORDER = ["user", "feedback", "project", "skill", "reference"];

export function MemoryGrid({ entries }: { entries: MemoryEntry[] }) {
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const onDeleted = (filename: string) =>
    setRemoved((s) => new Set(s).add(filename));

  const grouped = useMemo(() => {
    const visible = entries.filter((e) => !removed.has(e.filename));
    const map = new Map<string, MemoryEntry[]>();
    for (const e of visible) {
      const list = map.get(e.type) ?? [];
      list.push(e);
      map.set(e.type, list);
    }
    return TYPE_ORDER
      .map((t) => [t, map.get(t) ?? []] as const)
      .filter(([, list]) => list.length > 0);
  }, [entries, removed]);

  return (
    <div className="flex flex-col gap-6">
      {grouped.map(([type, list]) => (
        <div key={type}>
          <div className="flex items-baseline gap-2 mb-2 px-1">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              {type}
            </span>
            <span className="text-[11px] text-[var(--color-text-faint)]">{list.length}</span>
          </div>
          <Card className="overflow-hidden p-0">
            {list.map((e) => (
              <MemoryCard key={e.filename} entry={e} onDeleted={onDeleted} />
            ))}
          </Card>
        </div>
      ))}
    </div>
  );
}
