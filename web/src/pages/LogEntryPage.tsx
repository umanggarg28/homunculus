import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "@/lib/api";
import { BackLink } from "@/components/ui/BackLink";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";

export function LogEntryPage() {
  const { "*": rel = "" } = useParams();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rel) return;
    api.logEntry(rel).then(setText).catch((e) => setError(String(e)));
  }, [rel]);

  return (
    <PageShell>
      <BackLink to="/logs" label="Logs" />
      <PageHeader
        title={rel}
        subtitle={text ? `${(text.length / 1024).toFixed(1)} kb · ${text.split("\n").length} lines` : ""}
      />
      {error ? (
        <div
          className="px-4 py-3 text-[11px] uppercase tracking-[0.14em]"
          style={{
            border: "1px solid var(--color-danger)",
            color: "var(--color-danger)",
            fontFamily: "var(--font-mono)",
          }}
        >
          ● error · {error}
        </div>
      ) : text === null ? null : (
        <pre
          className="whitespace-pre-wrap break-words"
          style={{
            border: "1px solid var(--color-border)",
            background: "var(--color-surface-1)",
            color: "var(--color-text)",
            fontFamily: "var(--font-mono)",
            fontSize: 12.5,
            lineHeight: 1.65,
            padding: "16px 18px",
            margin: 0,
          }}
        >{text}</pre>
      )}
    </PageShell>
  );
}
