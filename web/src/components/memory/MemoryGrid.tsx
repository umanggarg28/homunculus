import { useMemo, useState } from "react";
import { MemoryCard } from "./MemoryCard";
import type { MemoryEntry } from "@/lib/types";

const TYPE_ORDER = ["user", "feedback", "project", "skill", "reference"];

/** Brutalist memory grid — sections labelled with `── TYPE`, hairline
 *  borders between rows, no rounded card wrapper. */
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
    <div className="flex flex-col gap-8" style={{ fontFamily: "var(--font-mono)" }}>
      {grouped.map(([type, list]) => (
        <div key={type}>
          <div
            className="flex items-baseline gap-3 mb-3 text-[10px] uppercase tracking-[0.22em]"
            style={{ color: "var(--color-text-muted)" }}
          >
            <span>── {type}</span>
            <span style={{ color: "var(--color-text-faint)" }}>
              {list.length.toString().padStart(2, "0")} entr{list.length === 1 ? "y" : "ies"}
            </span>
          </div>
          <div style={{ borderTop: "1px solid var(--color-border)" }}>
            {list.map((e) => (
              <MemoryCard key={e.filename} entry={e} onDeleted={onDeleted} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
