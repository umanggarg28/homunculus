import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api, parseServerIso } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { SignatureHeartbeat } from "@/components/overview/SignatureHeartbeat";
import { GrowthDeltas } from "@/components/overview/GrowthDeltas";
import { HomunculusRobot } from "@/components/robot/HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";
import type { MemoryEntry, Skill } from "@/lib/types";

/** Brutalist Overview — one signature element (the full-bleed
 *  heartbeat strip), one hero number, then dense readouts. */
export function OverviewPage() {
  const { events } = useEventStream(500);
  const robotState = useRobotState();
  const cycRef = useRef(0);
  const [cyc, setCyc] = useState(0);
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tasks, setTasks] = useState<Array<{ status: string }>>([]);

  useEffect(() => {
    api.memoryList().then(setMemories).catch(() => undefined);
    api.skillsList().then(setSkills).catch(() => undefined);
    api.tasksList("all").then(setTasks).catch(() => undefined);
    const t = setInterval(() => setCyc((c) => { cycRef.current = c + 1; return c + 1; }), 100);
    return () => clearInterval(t);
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
      <div className="mx-[-40px]"><SignatureHeartbeat /></div>
      <GrowthDeltas />

      {/* ── HERO ROW — countdown + robot panel ── */}
      <div
        className="grid gap-0 mb-10"
        style={{ gridTemplateColumns: "1fr 320px", border: "1px solid var(--color-border)" }}
      >
          {/* LEFT — countdown as hero + stats */}
          <div className="p-8 flex flex-col justify-center gap-0" style={{ borderRight: "1px solid var(--color-border)" }}>
            <UpcomingHero stats={stats} lastEvent={lastEvent} />
          </div>

          {/* RIGHT — robot panel */}
          <AgentPanel robotState={robotState} cyc={cyc} />
        </div>

        {/* ── STATE ── */}
        <div className="p-6 mb-10" style={{ border: "1px solid var(--color-border)" }}>
          <div className="text-[10px] uppercase tracking-[0.32em] mb-4" style={{ color: "var(--color-text-muted)" }}>
            ── state
          </div>
          <div
            className="grid gap-x-8 gap-y-3"
            style={{ gridTemplateColumns: "repeat(4, 1fr)" }}
          >
            <KV label="mcp servers"  value="02" hint="builtin · fetch" />
            <KV label="tools"        value={pad(skills.length)} hint={skillsCalled === skills.length ? "all ever called" : `${skillsCalled} ever called`} />
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

function AgentPanel({
  robotState,
  cyc,
}: {
  robotState: string;
  cyc: number;
}) {
  const cycStr = String(cyc % 10000).padStart(4, "0");
  const STATE_VERBS: Record<string, { verb: string; title: string }> = {
    idle:       { verb: "STATUS",    title: "awaiting input" },
    boot:       { verb: "BOOT",      title: "initialising" },
    listening:  { verb: "AUDIO",     title: "listening" },
    thinking:   { verb: "COGNITION", title: "thinking" },
    working:    { verb: "TASK",      title: "executing" },
    responding: { verb: "OUTPUT",    title: "responding" },
    success:    { verb: "DONE",      title: "complete" },
    error:      { verb: "ERROR",     title: "fault detected" },
  };
  const info = STATE_VERBS[robotState] ?? STATE_VERBS.idle;

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        background: "radial-gradient(ellipse at 50% 30%, rgba(124,254,0,0.03), transparent 70%)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px 16px",
        gap: 16,
      }}
    >
      {/* Framed robot */}
      <div style={{ position: "relative", width: "100%", maxWidth: 240, aspectRatio: "3/4", border: "1px solid var(--color-border)", background: "var(--color-surface-1)" }}>
        {/* HUD corners */}
        {(["tl","tr","bl","br"] as const).map((c) => (
          <span key={c} style={{
            position: "absolute", width: 10, height: 10,
            border: "1px solid var(--color-border-bright)",
            top: c[0] === "t" ? 6 : undefined, bottom: c[0] === "b" ? 6 : undefined,
            left: c[1] === "l" ? 6 : undefined, right: c[1] === "r" ? 6 : undefined,
            borderRight: c[1] === "r" ? "none" : undefined, borderLeft: c[1] === "l" ? "none" : undefined,
            borderBottom: c[0] === "t" ? "none" : undefined, borderTop: c[0] === "b" ? "none" : undefined,
          }} />
        ))}
        {/* HUD top */}
        <div style={{ position: "absolute", left: 10, right: 10, top: 9, display: "flex", justifyContent: "space-between", fontFamily: "var(--font-mono)", fontSize: 8, letterSpacing: "0.14em", color: "var(--color-text-muted)", textTransform: "uppercase", pointerEvents: "none" }}>
          <span>UNIT · <b style={{ color: "var(--color-accent)", fontWeight: 500 }}>HMCL-01</b></span>
          <span>UPLINK · <b style={{ color: "var(--color-accent)", fontWeight: 500 }}>OK</b></span>
        </div>
        <HomunculusRobot state={robotState as import("@/components/robot/HomunculusRobot").RobotState} detail="high" palette="phosphor" filled noDust style={{ width: "100%", height: "100%", display: "block" }} />
        {/* HUD bottom */}
        <div style={{ position: "absolute", left: 10, right: 10, bottom: 9, display: "flex", justifyContent: "space-between", fontFamily: "var(--font-mono)", fontSize: 8, letterSpacing: "0.14em", color: "var(--color-text-muted)", textTransform: "uppercase", pointerEvents: "none" }}>
          <span>STATE · <b style={{ color: "var(--color-accent)", fontWeight: 500 }}>{robotState.toUpperCase()}</b></span>
          <span>CYC · <b style={{ color: "var(--color-accent)", fontWeight: 500 }}>{cycStr}</b></span>
        </div>
      </div>

      {/* Caption */}
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 9, letterSpacing: "0.28em", color: "var(--color-text-muted)", textTransform: "uppercase", marginBottom: 4 }}>{info.verb}</div>
        <div style={{ fontSize: 14, letterSpacing: "0.04em", color: "var(--color-accent)", textShadow: "0 0 14px var(--color-accent-glow)" }}>{info.title}</div>
      </div>

    </div>
  );
}

// ── UpcomingHero ────────────────────────────────────────────────────

interface Upcoming {
  next_tick: string | null;
  default_interval_min: number;
  next_task: { id: string; title: string; due_at: string; recurrence: string } | null;
}

function parseIsoLocal(iso: string): number {
  return parseServerIso(iso);
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "00:00";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  return `${pad(m)}:${pad(sec)}`;
}

function fmtAbs(d: Date): string {
  const dd = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const tt = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${dd} · ${tt}`.toLowerCase();
}

function nowNarration(lastEvent: { event: string; tool?: string } | undefined): string {
  if (!lastEvent) return "─ no activity yet";
  switch (lastEvent.event) {
    case "llm_call": return "thinking…";
    case "tool_call": return `calling ${(lastEvent.tool ?? "tool").toLowerCase()}…`;
    case "tool_result": return "processing result…";
    case "assistant_reply": return "replied.";
    case "user_message": return "received input.";
    default: return lastEvent.event.replace(/_/g, " ");
  }
}

function UpcomingHero({
  stats,
  lastEvent,
}: {
  stats: { toolCalls: number; llmCalls: number; replies: number; failures: number };
  lastEvent: { event: string; ts: string; tool?: string } | undefined;
}) {
  const [data, setData] = useState<Upcoming | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const fetchOnce = () => api.agentUpcoming().then(setData).catch(() => undefined);
    fetchOnce();
    const slow = setInterval(fetchOnce, 10_000);
    const fast = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(slow); clearInterval(fast); };
  }, []);

  const target = (() => {
    if (!data) return null;
    const candidates: { ms: number; label: string; sub: string }[] = [];
    if (data.next_tick) {
      const ms = parseIsoLocal(data.next_tick);
      candidates.push({ ms, label: "scheduled heartbeat", sub: `${data.default_interval_min}min default` });
    }
    if (data.next_task) {
      const ms = parseIsoLocal(data.next_task.due_at);
      candidates.push({
        ms,
        label: data.next_task.title.toLowerCase(),
        sub: data.next_task.recurrence === "none" ? "one-shot" : data.next_task.recurrence,
      });
    }
    candidates.sort((a, b) => a.ms - b.ms);
    return candidates[0] ?? null;
  })();

  const STAT_COLS: { label: string; value: number; color: string }[] = [
    { label: "tool calls", value: stats.toolCalls, color: "var(--color-accent)" },
    { label: "replies",    value: stats.replies,   color: "#818cf8" },
    { label: "llm calls",  value: stats.llmCalls,  color: "var(--color-accent)" },
    { label: "failures",   value: stats.failures,  color: stats.failures > 0 ? "var(--color-danger)" : "var(--color-text-faint)" },
  ];

  return (
    <>
      <div className="text-[10px] uppercase tracking-[0.32em] mb-3" style={{ color: "var(--color-text-muted)" }}>
        ── next autonomous fire
      </div>

      {/* Hero countdown */}
      {target ? (() => {
        const delta = target.ms - now;
        const overdue = delta <= 0;
        return (
          <>
            <div
              style={{
                fontSize: "clamp(54px, 8vw, 104px)",
                lineHeight: 0.85,
                fontVariantNumeric: "tabular-nums",
                letterSpacing: "-0.04em",
                color: overdue ? "var(--color-amber)" : "var(--color-accent)",
                textShadow: overdue ? "0 0 24px rgba(255,176,0,0.4)" : "0 0 32px var(--color-accent-glow)",
                fontFamily: "var(--font-mono)",
                marginBottom: 12,
              }}
            >
              {overdue ? "OVERDUE" : formatCountdown(delta)}
            </div>
            <div className="text-[14px] mb-1" style={{ color: "var(--color-text)", fontFamily: "var(--font-mono)" }}>
              <span style={{ color: overdue ? "var(--color-amber)" : "var(--color-accent)" }}>›</span>{" "}{target.label}
            </div>
            <div className="text-[10px] uppercase tracking-[0.16em] mb-6" style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}>
              {target.sub} · {fmtAbs(new Date(target.ms))}
            </div>
          </>
        );
      })() : (
        <div
          style={{
            fontSize: "clamp(54px, 8vw, 104px)",
            lineHeight: 0.85,
            color: "var(--color-text-faint)",
            fontFamily: "var(--font-mono)",
            letterSpacing: "-0.04em",
            marginBottom: 20,
          }}
        >
          --:--
        </div>
      )}

      {/* Stats row — since midnight */}
      <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: 12, marginBottom: 6 }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.22em", textTransform: "uppercase", color: "var(--color-text-faint)", marginBottom: 10 }}>
          since midnight
        </div>
        <div className="grid grid-cols-4 gap-4" style={{ marginBottom: 16 }}>
          {STAT_COLS.map(({ label, value, color }) => (
            <div key={label}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--color-text-faint)", marginBottom: 4 }}>
                {label}
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 22, color, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}>
                {value.toString().padStart(2, "0")}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* NOW narration */}
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", color: "var(--color-text-dim)", borderTop: "1px solid var(--color-border)", paddingTop: 12 }}>
        <span style={{ color: "var(--color-text-faint)", marginRight: 8 }}>NOW</span>
        {nowNarration(lastEvent)}
      </div>
    </>
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
