import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { MemoryGrid } from "@/components/memory/MemoryGrid";
import { MemoryHero } from "@/components/ui/HeroBand";
import type { MemoryEntry } from "@/lib/types";

export function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);

  useEffect(() => {
    api.memoryList().then(setEntries).catch(() => setEntries([]));
  }, []);

  return (
    <PageShell>
      <PageHeader
        title="Memory"
        subtitle={entries ? `${entries.length} entries · grouped by type` : ""}
      />
      {entries && entries.length > 0 && <MemoryHero entries={entries} />}
      {entries === null ? null
        : entries.length === 0 ? (
            <BrutalistEmpty
              header="MEMORY EMPTY"
              body={<>the agent writes here as it learns — preferences, project context, references, feedback. it&apos;ll start populating after a few conversations.</>}
              samplesHeader="── SEED IT BY TELLING IT"
              samples={[
                "remember that I prefer terse responses with no trailing summaries",
                "I'm a senior engineer working on a real-time agent system",
                "the project root for homunculus is ~/Projects/homunculus",
              ]}
            />
          )
        : <MemoryGrid entries={entries} />}
    </PageShell>
  );
}
