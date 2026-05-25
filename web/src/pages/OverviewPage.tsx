import { useEffect, useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { UpcomingPanel } from "@/components/overview/UpcomingPanel";
import { LiveTicker } from "@/components/overview/LiveTicker";
import { SignatureHeartbeat } from "@/components/overview/SignatureHeartbeat";
import { GrowthDeltas } from "@/components/overview/GrowthDeltas";
import type { MemoryEntry, Skill } from "@/lib/types";

/** Brutalist Overview — one signature element (the full-bleed
 *  heartbeat strip), one hero number, then dense readouts. */
export function OverviewPage() {
  const { events } = useEventStream(500);
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tasks, setTasks] = useState<Array<{ status: string }>>([]);

  useEffect(() => {
    api.memoryList().then(setMemories).catch(() => undefined);
    api.skillsList().then(setSkills).catch(() => undefined);
    api.tasksList("all").then(setTasks).catch(() => undefined);
  }, []);

  const stats = useMemo(() => {
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const startMs = startOfDay.getTime();
    const today = events.filter((e) => new Date(e.ts).getTime() >= startMs);
    return {
      toolCalls: today.filter((e) => e.event === "tool_call").length,
      llmCalls:  today.filter((e) => e.event === "llm_call").length,
      replies:   today.filter((e) => e.event === "assistant_reply").length,
      failures:  today.filter(
        (e) => e.event === "tool_result" && typeof e.result === "string" && e.result.startsWith("ERROR"),
      ).length,
    };
  }, [events]);

  const lastEvent = events[events.length - 1];
  const lastAgeSec = lastEvent
    ? Math.floor((Date.now() - new Date(lastEvent.ts).getTime()) / 1000)
    : null;

  const activeTasks = tasks.filter((t) => t.status === "active").length;
  const skillsCalled = skills.filter((s) => s.call_count > 0).length;

  return (
    <PageShell>
      <PageHeader title="Overview" subtitle={liveStateMessage(lastAgeSec).toLowerCase()} />
      <LiveTicker />
      <div className="mx-[-40px]"><SignatureHeartbeat /></div>
      <GrowthDeltas />

      {/* ── HERO ROW — one big anchor + upcoming countdown beside it ── */}
      <div
        className="grid gap-6 mb-10"
        style={{ gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)" }}
      >
          {/* HERO — the one number that matters */}
          <div
            className="p-7 flex flex-col justify-between"
            style={{ background: "var(--color-surface-1)", border: "1px solid var(--color-border)" }}
          >
            <div
              className="text-[10px] uppercase tracking-[0.32em]"
              style={{ color: "var(--color-text-muted)" }}
            >
              ── actions today
            </div>
            <div className="flex items-end gap-5 my-2">
              <span
                style={{
                  color: "var(--color-accent)",
                  fontSize: "clamp(96px, 16vw, 192px)",
                  lineHeight: 0.82,
                  fontWeight: 700,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.05em",
                  textShadow: "0 0 32px var(--color-accent-glow)",
                }}
              >
                {stats.toolCalls.toString().padStart(2, "0")}
              </span>
              <span
                className="pb-4 text-[10px] uppercase tracking-[0.18em]"
                style={{ color: "var(--color-text-faint)", lineHeight: 1.5 }}
              >
                tool calls<br />since 00:00
              </span>
            </div>
            <div
              className="grid grid-cols-3 gap-6 text-[9px] uppercase tracking-[0.18em] pt-4"
              style={{ borderTop: "1px solid var(--color-border)" }}
            >
              <Inline label="replies"  value={stats.replies} />
              <Inline label="llm"      value={stats.llmCalls} />
              <Inline label="failures" value={stats.failures} danger={stats.failures > 0} />
            </div>
          </div>

          {/* Upcoming — secondary anchor */}
          <UpcomingPanel />
        </div>

        {/* ── STATE ── */}
        <div className="p-6 mb-10" style={{ border: "1px solid var(--color-border)" }}>
          <div className="text-[10px] uppercase tracking-[0.32em] mb-4" style={{ color: "var(--color-text-muted)" }}>
            ── state
          </div>
          <div
            className="grid gap-x-12 gap-y-3"
            style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}
          >
            <KV label="mcp servers"  value="02" hint="builtin · fetch" />
            <KV label="tools"        value={pad(skills.length)} hint={`${skillsCalled} ever called`} />
            <KV label="memory"       value={pad(memories.length)} hint="entries" />
            <KV label="active tasks" value={pad(activeTasks)} />
          </div>
        </div>

        {/* ── ACTIVITY TAIL ── */}
        <div className="p-6" style={{ border: "1px solid var(--color-border)" }}>
          <div className="text-[10px] uppercase tracking-[0.32em] mb-4" style={{ color: "var(--color-text-muted)" }}>
            ── activity · last 16 events
          </div>
          {events.length === 0 ? (
            <div className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-faint)" }}>
              ─ no events yet
            </div>
          ) : (
            <div className="text-[12px] leading-[1.85]">
              {events.slice(-16).reverse().map((e, i) => (
                <ActivityRow key={i} event={e} />
              ))}
            </div>
          )}
        </div>
    </PageShell>
  );
}

// ── helpers ─────────────────────────────────────────────────────────

function Inline({ label, value, danger }: { label: string; value: number; danger?: boolean }) {
  return (
    <div>
      <div style={{ color: "var(--color-text-faint)" }}>{label}</div>
      <div
        className="text-[22px] mt-1"
        style={{
          color: danger ? "var(--color-danger)" : "var(--color-text)",
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.02em",
        }}
      >
        {value.toString().padStart(2, "0")}
      </div>
    </div>
  );
}

function KV({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div
      className="flex items-baseline justify-between gap-4 py-2"
      style={{ borderBottom: "1px dashed var(--color-border)" }}
    >
      <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </span>
      <span className="flex items-baseline gap-3">
        {hint && (
          <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-faint)" }}>
            {hint}
          </span>
        )}
        <span
          className="text-[18px]"
          style={{ color: "var(--color-text)", fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}
        >
          {value}
        </span>
      </span>
    </div>
  );
}

function ActivityRow({
  event,
}: {
  event: { event: string; ts: string; tool?: string; result?: string };
}) {
  const time = new Date(event.ts);
  const hh = pad(time.getHours());
  const mm = pad(time.getMinutes());
  const ss = pad(time.getSeconds());
  const isErr =
    event.event === "tool_result" &&
    typeof event.result === "string" &&
    event.result.startsWith("ERROR");
  const dot =
    event.event === "tool_call" ? "→"
      : event.event === "tool_result" ? (isErr ? "✗" : "←")
      : event.event === "llm_call" ? "λ"
      : event.event === "assistant_reply" ? "›" : "·";
  const dotColor = isErr ? "var(--color-danger)" : "var(--color-accent)";
  const txtColor = isErr ? "var(--color-danger)" : "var(--color-text-dim)";
  return (
    <div className="grid items-baseline" style={{ gridTemplateColumns: "84px 18px 1fr", columnGap: 12 }}>
      <span style={{ color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums", fontSize: 11 }}>
        {hh}:{mm}:{ss}
      </span>
      <span style={{ color: dotColor }}>{dot}</span>
      <span style={{ color: txtColor }}>
        <span
          className="mr-3"
          style={{
            color: "var(--color-text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontSize: 10,
          }}
        >
          {event.event}
        </span>
        {event.tool ?? (isErr ? truncate(event.result ?? "", 80) : "")}
      </span>
    </div>
  );
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function liveStateMessage(ageSec: number | null): string {
  if (ageSec === null) return "AT REST · NO ACTIVITY";
  if (ageSec < 10) return "● ACTING RIGHT NOW";
  if (ageSec < 60) return `LAST ACTIVITY ${ageSec}s AGO`;
  if (ageSec < 3600) return `LAST ACTIVITY ${Math.floor(ageSec / 60)}m AGO`;
  return `LAST ACTIVITY ${Math.floor(ageSec / 3600)}h AGO`;
}
