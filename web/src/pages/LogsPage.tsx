import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { LogList } from "@/components/logs/LogList";
import { LogsHeatmap } from "@/components/logs/LogsHeatmap";
import { LogsHero } from "@/components/ui/HeroBand";
import type { LogFile } from "@/lib/types";

export function LogsPage() {
  const [logs, setLogs] = useState<LogFile[] | null>(null);

  useEffect(() => {
    api.logsList().then(setLogs).catch(() => setLogs([]));
  }, []);

  return (
    <PageShell>
      <PageHeader
        title="Logs"
        subtitle={logs ? `${logs.length} daily transcripts · newest first` : "Daily transcripts"}
      />
      {logs && logs.length > 0 && <LogsHero logs={logs} />}
      {logs === null ? null
        : logs.length === 0 ? (
            <BrutalistEmpty
              header="NO TRANSCRIPTS YET"
              body={<>every conversation gets archived here as a daily markdown file once you close the session. start a chat — the first transcript appears at <code style={{ color: "var(--color-text)" }}>workspace/logs/{new Date().toISOString().slice(0, 10)}.md</code>.</>}
            />
          )
        : (
            <>
              <LogsHeatmap logs={logs} />
              <LogList logs={logs} />
            </>
          )}
    </PageShell>
  );
}
