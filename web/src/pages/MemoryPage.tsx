import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { MemoryGrid } from "@/components/memory/MemoryGrid";
import { MemoryConstellation } from "@/components/memory/MemoryConstellation";
import { MemoryHero } from "@/components/ui/HeroBand";
import type { MemoryEntry } from "@/lib/types";

export function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [scanState, setScanState] = useState<string>("");

  useEffect(() => {
    api.memoryList().then(setEntries).catch(() => setEntries([]));
  }, []);

  return (
    <PageShell>
      <PageHeader
        title="Memory"
        subtitle={entries ? `${entries.length} entries · grouped by type` : ""}
      />
      {entries && entries.length > 0 && (
        <div className="instrument-panel hm-panel-scan hm-panel-secondary mb-4 px-4 py-3 flex items-center justify-between gap-3" style={{ fontFamily: "var(--font-mono)" }}>
          <div className="min-w-0">
            <div className="brut-label" style={{ color: "var(--color-text)" }}>memory consolidation</div>
            <div className="brut-meta mt-1" style={{ color: "var(--color-text-muted)" }}>
              find duplicate &amp; stale notes to review
            </div>
          </div>
          <button
            className="brut-label shrink-0"
            onClick={async () => {
              setScanState("scanning...");
              try {
                const res = await api.memoryConsolidationPropose(5);
                setScanState(`${res.created.length} proposal${res.created.length === 1 ? "" : "s"} filed`);
                window.dispatchEvent(new CustomEvent("hm:proposals-changed"));
              } catch (e) {
                setScanState(e instanceof Error ? e.message : String(e));
              }
            }}
            style={{
              border: "1px solid var(--color-accent)",
              color: "var(--color-accent)",
              background: "transparent",
              padding: "6px 10px",
              letterSpacing: "0.1em",
              cursor: "pointer",
            }}
          >
            scan
          </button>
          {scanState && <div className="brut-meta shrink-0" style={{ color: "var(--color-text-muted)" }}>{scanState}</div>}
        </div>
      )}
      {entries && entries.length > 0 && <MemoryHero entries={entries} />}
      {entries && entries.length > 0 && <MemoryConstellation entries={entries} />}
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
