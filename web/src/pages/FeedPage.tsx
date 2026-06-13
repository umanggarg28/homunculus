import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useEventStream } from "@/hooks/useEventStream";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { TracesTimeline } from "@/components/traces/TracesTimeline";
import { FeedStream } from "@/components/feed/FeedStream";
import { markTracesSeen } from "@/components/layout/AlertBanner";

/** Traces — the agent's activity stream rendered as a Gantt-style
 *  timeline. Horizontal time axis, 5 swimlanes (USER / LLM / TOOL /
 *  MEMORY / REPLY), each event a hairline-bordered block sized by
 *  duration. Click any block for the full record. Below the timeline,
 *  the raw event log persists for reference. */
export function FeedPage() {
  const { events } = useEventStream(160);
  const [now, setNow] = useState(() => Date.now());
  // ?at=<epoch ms> — deep link from a task run row; the timeline picks
  // a window containing that moment and marks it.
  const [searchParams] = useSearchParams();
  const atParam = Number(searchParams.get("at"));
  const focusTs = Number.isFinite(atParam) && atParam > 0 ? atParam : undefined;

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Opening Traces acknowledges the "check traces" alerts — they clear
  // from the top banner. Re-mark on each visit so newer errors reappear.
  useEffect(() => { markTracesSeen(); }, []);

  const SYSTEM_EVENTS_SET = new Set(["service_ping","provider_cooled","context_compacted","budget_blocked","agent_controls_updated"]);
  const lastEvent = [...events].reverse().find((e) => !SYSTEM_EVENTS_SET.has(e.event));
  const ageSec = lastEvent ? (now - new Date(lastEvent.ts).getTime()) / 1000 : Infinity;
  const live = ageSec < 5;

  return (
    <PageShell>
      <PageHeader
        title="Traces"
        subtitle={
          live
            ? "● acting right now"
            : ageSec < 60
              ? `last activity ${Math.floor(ageSec)}s ago`
              : ageSec < 3600
                ? `last activity ${Math.floor(ageSec / 60)}m ago`
                : "idle"
        }
      />

      <TracesTimeline focusTs={focusTs} />

      {/* Counts are owned by FeedStream's filter bar so they reflect the
          current toggle state (sys on/off, event-type chips, search) —
          a fixed count here was stale and made the SYS filter look broken. */}
      <div className="brut-meta mt-10 mb-3" style={{ color: "var(--color-text-muted)" }}>
        ── raw event log
      </div>
      <FeedStream />
    </PageShell>
  );
}
