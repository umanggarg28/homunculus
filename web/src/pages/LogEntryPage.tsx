import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "@/lib/api";
import { BackLink } from "@/components/ui/BackLink";
import { PageHeader } from "@/components/ui/PageHeader";

export function LogEntryPage() {
  const { "*": rel = "" } = useParams();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rel) return;
    api.logEntry(rel).then(setText).catch((e) => setError(String(e)));
  }, [rel]);

  return (
    <div className="max-w-[860px] mx-auto px-8 pt-10 pb-16">
      <BackLink to="/logs" label="Logs" />
      <PageHeader title={rel} />
      {error ? (
        <div
          className="text-[12.5px]"
          style={{ color: "var(--color-danger)", fontFamily: "var(--font-mono)" }}
        >
          {error}
        </div>
      ) : text === null ? null : (
        <pre
          className="rounded-[8px] p-4 mono whitespace-pre-wrap break-words leading-relaxed"
          style={{
            border: "1px solid var(--color-border)",
            background: "var(--color-surface-2)",
            color: "var(--color-text)",
            fontSize: 12.5,
          }}
        >{text}</pre>
      )}
    </div>
  );
}
