import { useEffect, useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api } from "@/lib/api";

interface Alert {
  level: "danger" | "warn";
  text: string;
}

/** Contextual alert strip — surfaces hard failures (recent tool
 *  errors, missing API keys, failed task runs) at the top of every
 *  page. Hairline border in danger/amber, hides itself when clean. */
export function AlertBanner() {
  const { events } = useEventStream(200);
  const [tasksFailed, setTasksFailed] = useState(0);

  useEffect(() => {
    api.tasksList("all").then((tasks) => {
      const startOfToday = (() => { const d = new Date(); d.setHours(0,0,0,0); return d.getTime(); })();
      let failed = 0;
      for (const t of tasks) {
        for (const r of (t.last_runs || [])) {
          if (r.status === "failure" && new Date(r.ts).getTime() >= startOfToday) failed += 1;
        }
      }
      setTasksFailed(failed);
    }).catch(() => undefined);
  }, [events.length]);

  const alerts = useMemo<Alert[]>(() => {
    const out: Alert[] = [];
    // Recent tool failures (last 5 minutes)
    const cutoff = Date.now() - 5 * 60 * 1000;
    const recentErr = events.filter(
      (e) =>
        e.event === "tool_result" &&
        typeof e.result === "string" &&
        e.result.startsWith("ERROR") &&
        new Date(e.ts).getTime() >= cutoff,
    );
    if (recentErr.length > 0) {
      out.push({
        level: "danger",
        text: `${recentErr.length} tool error${recentErr.length > 1 ? "s" : ""} in the last 5 min · check traces`,
      });
    }
    if (tasksFailed > 0) {
      out.push({
        level: "warn",
        text: `${tasksFailed} task run${tasksFailed > 1 ? "s" : ""} failed today · check task detail`,
      });
    }
    return out;
  }, [events, tasksFailed]);

  if (alerts.length === 0) return null;

  return (
    <div style={{ fontFamily: "var(--font-mono)" }}>
      {alerts.map((a, i) => (
        <div
          key={i}
          className="px-10 py-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em]"
          style={{
            background: "var(--color-bg)",
            borderBottom: `1px solid ${a.level === "danger" ? "var(--color-danger)" : "var(--color-amber)"}`,
            color: "var(--color-text-dim)",
          }}
        >
          <span style={{ color: a.level === "danger" ? "var(--color-danger)" : "var(--color-amber)" }}>
            ● {a.level === "danger" ? "alert" : "warn"}
          </span>
          <span style={{ color: "var(--color-border-strong)" }}>──</span>
          <span>{a.text}</span>
        </div>
      ))}
    </div>
  );
}
