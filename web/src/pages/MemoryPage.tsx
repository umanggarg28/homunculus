import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Empty } from "@/components/ui/Empty";
import { MemoryGrid } from "@/components/memory/MemoryGrid";
import type { MemoryEntry } from "@/lib/types";

export function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);

  useEffect(() => {
    api.memoryList().then(setEntries).catch(() => setEntries([]));
  }, []);

  return (
    <div className="max-w-[1000px] mx-auto px-8 pt-10 pb-16">
      <PageHeader
        title="Memory"
        subtitle={entries ? `${entries.length} entries · grouped by type` : ""}
      />
      {entries === null ? null
        : entries.length === 0 ? <Empty>No memory entries yet. The agent writes here as it learns.</Empty>
        : <MemoryGrid entries={entries} />}
    </div>
  );
}
