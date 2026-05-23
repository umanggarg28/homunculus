import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Empty } from "@/components/ui/Empty";
import { LogList } from "@/components/logs/LogList";
import type { LogFile } from "@/lib/types";

export function LogsPage() {
  const [logs, setLogs] = useState<LogFile[] | null>(null);

  useEffect(() => {
    api.logsList().then(setLogs).catch(() => setLogs([]));
  }, []);

  return (
    <div className="max-w-[1000px] mx-auto px-8 pt-10 pb-16">
      <PageHeader
        title="Logs"
        subtitle={logs ? `${logs.length} daily transcripts · newest first` : "Daily transcripts"}
      />
      {logs === null ? null
        : logs.length === 0 ? <Empty>No transcripts yet.</Empty>
        : <LogList logs={logs} />}
    </div>
  );
}
