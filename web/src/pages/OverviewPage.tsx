import { useEffect, useMemo, useRef, useState } from "react";
import { formatCents } from "@/lib/format";
import { ContainmentPanel } from "@/components/overview/ContainmentPanel";
import { SkillProposals } from "@/components/overview/SkillProposals";
import { TransmissionsFeed } from "@/components/overview/TransmissionsFeed";
import { useEventStream } from "@/hooks/useEventStream";
import { api, parseServerIso } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { TickingDigits } from "@/components/ui/TickingDigits";
import { Tooltip } from "@/components/ui/Tooltip";
import { SignatureHeartbeat } from "@/components/overview/SignatureHeartbeat";
import { HomunculusRobot } from "@/components/robot/HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";
import type { FeedEvent, MemoryEntry, Skill } from "@/lib/types";

interface TodayStats {
  events: number;
  unique_tools: number;
  tasks_fired: number;
  memory_writes: number;
  memory_forgets: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_cents: number;
}

interface CtxData {
  used_tokens: number;
  limit_tokens: number;
  model: string;
  pct: number;
}

/** Brutalist Overview — one signature element (the full-bleed
 *  heartbeat strip), one hero number, then dense readouts. */
export function OverviewPage() {
  const { events } = useEventStream(500);
  const robotState = useRobotState();
  const cycRef = useRef(0);
  const [cyc, setCyc] = useState(0);
  const [memories, setMemories] = useState<MemoryEntry[] | null>(null);
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [tasks, setTasks] = useState<Array<{ status: string }> | null>(null);
  const [todayStats, setTodayStats] = useState<TodayStats | null>(null);
  const [contextData, setContextData] = useState<CtxData | null>(null);

  useEffect(() => {
    api.memoryList().then(setMemories).catch(() => undefined);
    api.skillsList().then(setSkills).catch(() => undefined);
    api.tasksList("all").then(setTasks).catch(() => undefined);
    const fetchStats = () => api.statsToday().then(setTodayStats).catch(() => undefined);
    const fetchContext = () => api.contextGauge().then(setContextData).catch(() => undefined);
    fetchStats();
    fetchContext();
    const statsTimer = setInterval(fetchStats, 30_000);
    const contextTimer = setInterval(fetchContext, 30_000);
    const t = setInterval(() => setCyc((c) => { cycRef.current = c + 1; return c + 1; }), 100);
    return () => {
      clearInterval(t);
      clearInterval(statsTimer);
      clearInterval(contextTimer);
    };
  }, []);

  const stats = useMemo(() => {
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const startMs = startOfDay.getTime();
    const today = events.filter((e) => new Date(e.ts).getTime() >= startMs);
    return {
      failures:  today.filter(
        (e) => e.event === "tool_result" && typeof e.result === "string" && e.result.startsWith("ERROR"),
      ).length,
    };
  }, [events]);

  const SYSTEM_EVENTS = new Set(["service_ping","provider_cooled","context_compacted","budget_blocked","agent_controls_updated"]);
  const lastEvent = [...events].reverse().find((e) => !SYSTEM_EVENTS.has(e.event));
  const lastAgeSec = lastEvent
    ? Math.floor((Date.now() - new Date(lastEvent.ts).getTime()) / 1000)
    : null;

  const derivedTodayStats = useMemo(() => deriveTodayStats(events), [events]);
  const displayTodayStats = todayStats ?? derivedTodayStats;
  const activeTasks = tasks ? tasks.filter((t) => t.status === "active").length : null;
  const readyItems = useMemo(
    () => buildReadiness({
      lastAgeSec,
      failures: stats.failures,
      activeTasks,
      skills,
    }),
    [activeTasks, lastAgeSec, skills, stats.failures],
  );

  return (
    <PageShell>
      <PageHeader title="Overview" subtitle={liveStateMessage(lastAgeSec).toLowerCase()} />
      <style>{`
        .overview-command-deck {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 320px;
          margin-bottom: 40px;
          overflow: hidden;
          background:
            radial-gradient(circle at 13% 18%, rgba(124, 254, 0, 0.075), transparent 26%),
            linear-gradient(180deg, rgba(119, 255, 61, 0.035), rgba(108, 231, 255, 0.012)),
            var(--color-surface-1);
          box-shadow:
            inset 0 1px 0 rgba(215, 245, 223, 0.045),
            inset 0 -1px 0 rgba(124, 254, 0, 0.025),
            0 18px 64px rgba(0, 0, 0, 0.32),
            0 0 28px rgba(124, 254, 0, 0.035);
        }
        .overview-command-main {
          border-right: 1px solid var(--color-border);
        }
        .overview-command-agent {
          min-width: 0;
        }
        .overview-command-status {
          grid-column: 1 / -1;
          border-top: 1px solid var(--color-border);
          padding: 18px;
          background:
            linear-gradient(90deg, rgba(124,254,0,0.025), transparent 42%),
            var(--color-surface-1);
        }
        .overview-command-status-grid {
          display: grid;
          grid-template-columns: minmax(0, 0.82fr) minmax(320px, 1.18fr);
          gap: 14px 18px;
          align-items: start;
        }
        .overview-pulse-panel {
          grid-column: 1 / -1;
          min-width: 0;
          border: 1px solid var(--color-border);
          padding: 14px 16px;
          background: linear-gradient(90deg, rgba(124,254,0,0.025), transparent 55%);
        }
        .overview-today-ledger {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          border: 1px solid var(--color-border);
          align-self: stretch;
          min-height: 100%;
        }
        .overview-today-ledger > div {
          padding: 18px 20px;
          border-left: 1px solid var(--color-border);
          border-top: 1px solid var(--color-border);
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          gap: 18px;
        }
        .overview-today-ledger > div:nth-child(odd) {
          border-left: none;
        }
        .overview-today-ledger > div:nth-child(-n + 2) {
          border-top: none;
        }
        .overview-status-stack {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          border: 1px solid var(--color-border);
          min-width: 0;
        }
        .overview-status-wide {
          grid-column: 1 / -1;
        }
        .overview-status-stack > div {
          padding: 14px;
          border-left: 1px solid var(--color-border);
          border-top: 1px solid var(--color-border);
        }
        .overview-status-stack > div:nth-child(odd) {
          border-left: none;
        }
        .overview-status-stack > div:nth-child(-n + 2) {
          border-top: none;
        }
        .overview-status-wide {
          border-left: none !important;
        }
        .overview-context-bar {
          margin-top: 10px;
          height: 4px;
          background: var(--color-border);
          overflow: hidden;
        }
        .overview-context-bar > span {
          display: block;
          height: 100%;
          background: var(--color-accent);
          box-shadow: 0 0 10px var(--color-accent-glow);
        }
        @media (max-width: 980px) {
          .overview-command-deck {
            grid-template-columns: 1fr;
          }
          .overview-command-main {
            border-right: none;
            border-bottom: 1px solid var(--color-border);
          }
          .overview-command-status-grid {
            grid-template-columns: 1fr;
          }
          .overview-pulse-panel {
            grid-column: auto;
          }
        }
        @media (max-width: 560px) {
          .overview-command-main {
            padding: 20px !important;
          }
          .overview-command-status {
            padding: 14px;
          }
          .overview-status-stack {
            grid-template-columns: 1fr;
          }
          .overview-today-ledger {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .overview-status-stack > div,
          .overview-status-stack > div:nth-child(odd),
          .overview-status-stack > div:nth-child(-n + 2) {
            border-left: none;
            border-top: 1px solid var(--color-border);
          }
          .overview-status-stack > div:first-child {
            border-top: none;
          }
          .overview-status-wide {
            grid-column: auto;
          }
        }
        .overview-mission-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.25fr) minmax(260px, 0.75fr);
          gap: 18px;
        }
        .overview-ops-card {
          border: 1px solid var(--color-border);
          background: linear-gradient(180deg, rgba(119,255,61,0.018), transparent), var(--color-surface-1);
          min-width: 0;
          overflow: hidden;
          transition: border-color 180ms ease, box-shadow 220ms ease;
        }
        .overview-ops-card:hover,
        .overview-ops-card:focus-within {
          border-color: rgba(67, 133, 105, 0.76);
          box-shadow:
            inset 0 1px 0 rgba(215,245,223,0.03),
            0 14px 46px rgba(0,0,0,0.24),
            0 0 20px rgba(124,254,0,0.028);
        }
        .overview-ops-head {
          display: flex;
          justify-content: space-between;
          gap: 18px;
          align-items: baseline;
          padding: 16px 18px;
          border-bottom: 1px solid var(--color-border);
        }
        .overview-ops-body {
          padding: 18px;
        }
        .overview-run-summary {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          border-bottom: 1px solid var(--color-border);
        }
        .overview-run-summary > div {
          padding: 12px 14px;
          border-left: 1px solid var(--color-border);
        }
        .overview-run-summary > div:first-child {
          border-left: none;
        }
        .overview-step-row {
          display: grid;
          grid-template-columns: 74px 22px minmax(0, 1fr) auto;
          gap: 10px;
          align-items: baseline;
          padding: 8px 0;
          border-bottom: 1px dashed rgba(67,133,105,0.35);
        }
        .overview-tool-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(72px, 0.8fr) 34px;
          gap: 10px;
          align-items: center;
          padding: 6px 0;
        }
        @media (max-width: 920px) {
          .overview-mission-grid {
            grid-template-columns: 1fr;
          }
        }
        @media (max-width: 560px) {
          .overview-ops-card {
            padding: 0;
          }
          .overview-ops-head,
          .overview-ops-body {
            padding-left: 14px;
            padding-right: 14px;
          }
          .overview-run-summary {
            grid-template-columns: 1fr;
          }
          .overview-run-summary > div {
            border-left: none;
            border-top: 1px solid var(--color-border);
          }
          .overview-run-summary > div:first-child {
            border-top: none;
          }
          .overview-step-row {
            grid-template-columns: 56px 18px minmax(0, 1fr);
          }
          .overview-step-meta {
            grid-column: 3;
            justify-self: start !important;
          }
        }
      `}</style>

      {/* ── COMMAND DECK — next action + live state + operational truth ── */}
      <div
        className="overview-command-deck instrument-panel hm-panel-scan hm-panel-hero gap-0 hm-stagger"
        style={{ "--i": 0 } as React.CSSProperties}
      >
          {/* LEFT — countdown as hero + stats */}
          <div className="overview-command-main p-8 flex flex-col justify-center gap-0">
            <UpcomingHero stats={stats} lastEvent={lastEvent} />
          </div>

          {/* RIGHT — robot panel */}
          <div className="overview-command-agent">
            <AgentPanel robotState={robotState} cyc={cyc} />
          </div>

          <div className="overview-command-status">
            <CommandStatus
              readyItems={readyItems}
              todayStats={displayTodayStats}
              contextData={contextData}
              memories={memories?.length ?? null}
            />
          </div>
        </div>

      <div className="hm-stagger" style={{ "--i": 1 } as React.CSSProperties}>
        <RunInspector events={events} />
      </div>
      <div className="hm-stagger" style={{ "--i": 2 } as React.CSSProperties}>
        <SkillProposals />
      </div>
      <div className="hm-stagger" style={{ "--i": 3 } as React.CSSProperties}>
        <ContainmentPanel />
      </div>
      <div className="hm-stagger" style={{ "--i": 4 } as React.CSSProperties}>
        <TransmissionsFeed />
      </div>
    </PageShell>
  );
}

// ── helpers ─────────────────────────────────────────────────────────


function CommandStatus({
  readyItems,
  todayStats,
  contextData,
  memories,
}: {
  readyItems: Array<{ label: string; value: string; hint: string; tone: "ok" | "warn" | "idle" }>;
  todayStats: TodayStats | null;
  contextData: CtxData | null;
  memories: number | null;
}) {
  return (
    <div className="overview-command-status-grid">
      <div className="overview-pulse-panel">
        <SignatureHeartbeat />
      </div>
      <TodayLedger stats={todayStats} />
      <div className="overview-status-stack">
        {readyItems.map((item) => (
          <StatusCell key={item.label} {...item} />
        ))}
        <StatusCell
          label="runtime"
          value={todayStats ? `${todayStats.events}` : "--"}
          hint={todayStats ? `${todayStats.unique_tools} tools · ${todayStats.tasks_fired} fires` : "loading"}
          tone={todayStats && todayStats.events > 0 ? "ok" : "idle"}
        />
        <StatusCell
          label="memory"
          value={memories === null ? "SYNC" : pad(memories)}
          hint={todayStats ? `${Math.max(0, todayStats.memory_writes - todayStats.memory_forgets)} net today` : "persistent"}
          tone={memories && memories > 0 ? "ok" : "idle"}
        />
        <ContextStatusCell contextData={contextData} />
      </div>
    </div>
  );
}

function TodayLedger({ stats }: { stats: TodayStats | null }) {
  const tokens = stats ? (stats.input_tokens ?? 0) + (stats.output_tokens ?? 0) : 0;
  const cost = stats ? stats.cost_cents ?? 0 : 0;
  return (
    <div className="overview-today-ledger">
      <LedgerCell label="events" value={stats ? String(stats.events) : "--"} />
      <LedgerCell label="tools" value={stats ? String(stats.unique_tools) : "--"} />
      <LedgerCell label="fires" value={stats ? String(stats.tasks_fired) : "--"} />
      <LedgerCell label="spend" value={stats ? formatCost(cost, tokens) : "--"} />
    </div>
  );
}

function deriveTodayStats(events: FeedEvent[]): TodayStats {
  const startOfDay = new Date();
  startOfDay.setHours(0, 0, 0, 0);
  const startMs = startOfDay.getTime();
  const today = events.filter((e) => new Date(e.ts).getTime() >= startMs);
  const tools = new Set(today.filter((e) => e.event === "tool_call" && e.name).map((e) => e.name as string));
  return {
    events: today.length,
    unique_tools: tools.size,
    tasks_fired: today.filter((e) => ["task_fired", "task_started", "scheduled_task"].includes(e.event)).length,
    memory_writes: today.filter((e) => e.event === "memory_write").length,
    memory_forgets: today.filter((e) => e.event === "memory_forget").length,
    input_tokens: today.reduce((sum, e) => sum + (e.input_tokens ?? 0), 0),
    output_tokens: today.reduce((sum, e) => sum + (e.output_tokens ?? 0), 0),
    cached_tokens: today.reduce((sum, e) => sum + (e.cached_tokens ?? 0), 0),
    cost_cents: 0,
  };
}

function LedgerCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </div>
      {/* Ledger numbers are secondary — no text-shadow so they don't
          compete with the page-anchor countdown above. */}
      <div className="text-[34px] leading-none" style={{ color: "var(--color-accent)", fontVariantNumeric: "tabular-nums", letterSpacing: "0" }}>
        {value}
      </div>
    </div>
  );
}

function formatCost(costCents: number, tokens: number): string {
  if (costCents > 0) return formatCents(costCents);
  if (tokens > 0) return formatCents(0);
  return "idle";
}

function ContextStatusCell({ contextData }: { contextData: CtxData | null }) {
  const hasContext = !!contextData && contextData.used_tokens > 0;
  const pct = hasContext ? Math.min(contextData.pct, 100) : 0;
  const tone = hasContext && pct >= 70 ? "warn" : hasContext ? "ok" : "idle";
  const color = tone === "warn"
    ? "var(--color-amber)"
    : tone === "ok"
      ? "var(--color-accent)"
      : "var(--color-text-faint)";
  const fillColor = tone === "warn" ? "var(--color-amber)" : "var(--color-accent)";
  const tip = hasContext ? (
    <>
      <strong>{contextData.used_tokens.toLocaleString()}</strong> of {contextData.limit_tokens.toLocaleString()} tokens
      on <strong>{contextData.model}</strong>. Not cost — this measures how full the model's context window is.
      <br />
      Tool results from prior turns are auto-evicted to keep this low. Hard summarisation kicks in past 8 user turns.
    </>
  ) : (
    <>Context window usage. No model call yet this session — the gauge will populate after the first chat turn.</>
  );
  return (
    <Tooltip text={tip} placement="bottom">
      <div className="overview-status-wide hm-info hm-info--bare">
        <div className="flex items-end justify-between gap-4">
          <div>
            <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-muted)" }}>
              context
            </div>
            <div className="mt-2 text-[22px] leading-none" style={{ color, fontVariantNumeric: "tabular-nums", letterSpacing: "0" }}>
              {hasContext ? `${pct.toFixed(0)}%` : "COLD"}
            </div>
          </div>
          <div className="text-[9px] uppercase tracking-[0.1em] text-right" style={{ color: "var(--color-text-faint)", overflowWrap: "anywhere" }}>
            {hasContext ? compactTokens(contextData.used_tokens, contextData.limit_tokens) : "no model call"}
          </div>
        </div>
        <div className="overview-context-bar">
          <span style={{ width: `${pct}%`, background: fillColor }} />
        </div>
      </div>
    </Tooltip>
  );
}

function StatusCell({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "ok" | "warn" | "idle";
}) {
  const color = tone === "warn"
    ? "var(--color-amber)"
    : tone === "ok"
      ? "var(--color-accent)"
      : "var(--color-text-faint)";
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <span
          className="text-[22px] leading-none"
          style={{ color, fontVariantNumeric: "tabular-nums", letterSpacing: "0" }}
        >
          {value}
        </span>
        <span
          className="text-[9px] uppercase tracking-[0.1em] text-right"
          style={{ color: "var(--color-text-faint)", overflowWrap: "anywhere" }}
        >
          {hint}
        </span>
      </div>
    </div>
  );
}

function compactTokens(used: number, limit: number): string {
  return `${fmtShort(used)} / ${fmtShort(limit)}`;
}

function fmtShort(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

function buildReadiness({
  lastAgeSec,
  failures,
  activeTasks,
  skills,
}: {
  lastAgeSec: number | null;
  failures: number;
  activeTasks: number | null;
  skills: Skill[] | null;
  // memories param removed — capability cell now reflects tool usage only.
}) {
  const usedTools = skills?.filter((s) => s.call_count > 0).length ?? null;
  const failedTools = skills?.filter((s) => s.failure_count > 0).length ?? 0;
  const activeRecently = lastAgeSec !== null && lastAgeSec < 300;
  return [
    {
      label: "activity",
      value: activeRecently ? "LIVE" : "IDLE",
      hint: lastAgeSec === null ? "no events" : lastAgeSec < 60 ? `${lastAgeSec}s ago` : `${Math.floor(lastAgeSec / 60)}m ago`,
      tone: activeRecently ? "ok" : "idle",
    },
    {
      label: "attention",
      value: failures > 0 || failedTools > 0 ? "CHECK" : "CLEAR",
      hint: failures > 0 ? `${failures} failures today` : failedTools > 0 ? `${failedTools} tools failed` : "no failures",
      tone: failures > 0 || failedTools > 0 ? "warn" : "ok",
    },
    {
      label: "autonomy",
      value: activeTasks === null ? "SYNC" : activeTasks > 0 ? "ARMED" : "EMPTY",
      hint: activeTasks === null ? "loading tasks" : activeTasks > 0 ? `${activeTasks} active tasks` : "no active tasks",
      tone: activeTasks && activeTasks > 0 ? "ok" : "idle",
    },
    {
      // Value = tools actually called at least once / total tools registered.
      // Hint must describe the SAME thing — was mis-pointed at memory count.
      label: "capability",
      value: skills === null ? "SYNC" : usedTools && usedTools > 0 ? `${pad(usedTools)}/${pad(skills.length)}` : "COLD",
      hint: skills === null ? "loading tools" : usedTools && usedTools > 0
        ? `tools exercised`
        : "no tool calls yet",
      tone: usedTools && usedTools > 0 ? "ok" : "idle",
    },
  ] satisfies Array<{ label: string; value: string; hint: string; tone: "ok" | "warn" | "idle" }>;
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
        <div style={{ fontSize: 14, letterSpacing: "0.04em", color: "var(--color-accent)" }}>{info.title}</div>
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

function nowNarration(lastEvent: { event: string; name?: string; ts: string } | undefined, nowMs: number): string {
  if (!lastEvent) return "─ no activity yet";
  const ageSec = (nowMs - new Date(lastEvent.ts).getTime()) / 1000;
  // In-progress labels are only meaningful while the event is recent.
  // After 30s with no follow-up, the agent has finished (or crashed);
  // show the elapsed time instead of a stale "thinking…" / "calling…".
  const inProgress = ageSec < 30;
  switch (lastEvent.event) {
    case "llm_call":    return inProgress ? "thinking…"   : `idle · last active ${ageSec < 3600 ? `${Math.floor(ageSec / 60)}m` : `${Math.floor(ageSec / 3600)}h`} ago`;
    case "tool_call":   return inProgress ? `calling ${(lastEvent.name ?? "tool").toLowerCase()}…` : `idle · last active ${Math.floor(ageSec / 60)}m ago`;
    case "tool_result": return inProgress ? "processing result…" : "idle.";
    case "assistant_reply": return "replied.";
    case "user_message": return "received input.";
    default: return lastEvent.event.replace(/_/g, " ");
  }
}

function UpcomingHero({
  stats,
  lastEvent,
}: {
  stats: { failures: number };
  lastEvent: { event: string; ts: string; name?: string } | undefined;
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
              className="brut-display hm-countdown-glow"
              style={{
                fontSize: "clamp(54px, 8vw, 104px)",
                lineHeight: 0.85,
                fontWeight: 700,
                fontVariantNumeric: "tabular-nums",
                letterSpacing: "0",
                color: overdue ? "var(--color-amber)" : "var(--color-accent)",
                textShadow: overdue
                  ? "0 0 18px var(--color-amber), 0 0 4px var(--color-amber)"
                  : "0 0 18px var(--color-accent), 0 0 4px var(--color-accent)",
                marginBottom: 12,
              }}
            >
              {overdue ? "OVERDUE" : <TickingDigits text={formatCountdown(delta)} />}
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
            letterSpacing: "0",
            marginBottom: 20,
          }}
        >
          --:--
        </div>
      )}

      {/* Operational strip — do not duplicate the global since-midnight band above. */}
      <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: 12, marginBottom: 6 }}>
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", marginBottom: 16 }}>
          <HeroSignal
            label="last action"
            value={lastEvent ? lastEvent.event.replace(/_/g, " ") : "none"}
            tone={lastEvent ? "ok" : "idle"}
          />
          <HeroSignal
            label="today"
            value={stats.failures > 0 ? `${stats.failures} failures` : "no failures"}
            tone={stats.failures > 0 ? "warn" : "ok"}
          />
          <HeroSignal
            label="inspect"
            value={stats.failures > 0 ? "traces" : "chat"}
            tone={stats.failures > 0 ? "warn" : "idle"}
          />
        </div>
      </div>

      {/* NOW narration */}
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", color: "var(--color-text-dim)", borderTop: "1px solid var(--color-border)", paddingTop: 12 }}>
        <span style={{ color: "var(--color-text-faint)", marginRight: 8 }}>NOW</span>
        {nowNarration(lastEvent, now)}
      </div>
    </>
  );
}

function HeroSignal({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "idle";
}) {
  const color = tone === "warn"
    ? "var(--color-amber)"
    : tone === "ok"
      ? "var(--color-accent)"
      : "var(--color-text-muted)";
  return (
    <div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--color-text-faint)", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color, letterSpacing: "0.02em", textTransform: "uppercase", overflowWrap: "anywhere" }}>
        {value}
      </div>
    </div>
  );
}

function RunInspector({ events }: { events: FeedEvent[] }) {
  const recent = events.slice(-80);
  const turn = latestTurn(recent);
  const attention = recent
    .filter((e) => isAttentionEvent(e))
    .slice(-4)
    .reverse();
  const tools = toolMix(recent);
  const lastModel = [...recent].reverse().find((e) => e.event === "llm_call");
  const runService = turn.steps[0]?.service ?? turn.user?.service ?? lastModel?.service ?? "idle";
  const runAge = turn.steps.length
    ? `${Math.max(0, Math.floor((Date.now() - new Date(turn.steps[turn.steps.length - 1].ts).getTime()) / 1000))}s ago`
    : "no run";

  // Sparse mode: on a quiet day there is nothing in any of the panels —
  // no recent turn, no attention items, no tool calls, no model trace.
  // Rendering the full two-card grid in that state is just a wall of
  // "no X in view" placeholders. Collapse to a single calm idle pill
  // until activity resumes. The grid returns immediately on any signal.
  const isQuiet =
    !turn.user &&
    turn.steps.length === 0 &&
    attention.length === 0 &&
    tools.length === 0 &&
    !lastModel;

  if (isQuiet) {
    const lastTs = recent.at(-1)?.ts;
    const idleFor = lastTs
      ? Math.max(0, Math.floor((Date.now() - new Date(lastTs).getTime()) / 60000))
      : null;
    return (
      <div
        className="mb-10"
        style={{
          border: "1px solid var(--color-border)",
          padding: "18px 20px",
          fontFamily: "var(--font-mono)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div
            className="text-[10px] uppercase tracking-[0.28em]"
            style={{ color: "var(--color-text-muted)" }}
          >
            mission control
          </div>
          <div
            className="text-[12px] mt-1"
            style={{ color: "var(--color-text-dim)" }}
          >
            system idle — no runs, tool calls, or attention items in view
          </div>
        </div>
        <div
          className="text-[10px] uppercase tracking-[0.18em]"
          style={{ color: "var(--color-text-faint)" }}
        >
          {idleFor === null ? "no recent events" : idleFor === 0 ? "quiet · <1 min" : `quiet · ${idleFor} min`}
        </div>
      </div>
    );
  }

  return (
    <div className="overview-mission-grid mb-10">
      <div className="overview-ops-card">
        <div className="overview-ops-head">
          <div>
            <div className="text-[10px] uppercase tracking-[0.28em]" style={{ color: "var(--color-text-muted)" }}>
              run inspector
            </div>
            <div className="text-[11px] mt-1" style={{ color: "var(--color-text-faint)" }}>
              latest directive · execution trace · reply
            </div>
          </div>
          <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color: turn.steps.length ? "var(--color-accent)" : "var(--color-text-faint)" }}>
            {turn.steps.length ? `${turn.steps.length} steps` : "idle"}
          </div>
        </div>

        <div className="overview-run-summary">
          <SummaryCell label="service" value={runService} />
          <SummaryCell label="last event" value={turn.steps.at(-1)?.event.replace(/_/g, " ") ?? "none"} />
          <SummaryCell label="age" value={runAge} />
        </div>

        <div className="overview-ops-body">
          {turn.user ? (
            <div style={{ marginBottom: 14 }}>
              <div className="text-[10px] uppercase tracking-[0.18em] mb-2" style={{ color: "var(--color-text-faint)" }}>
                latest directive
              </div>
              <div
                className="text-[13px] leading-[1.65]"
                style={{ color: "var(--color-text)", overflowWrap: "anywhere", wordBreak: "break-word" }}
              >
                <span style={{ color: "var(--color-accent)" }}>›</span>{" "}
                {clip(turn.user.text ?? "", 150)}
              </div>
            </div>
          ) : (
            <EmptyLine text="no user directive in the current event window" />
          )}

          <div>
            {turn.steps.map((step, i) => (
              <MissionStep key={`${step.ts}-${i}`} event={step} />
            ))}
          </div>

          {turn.reply && (
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--color-border)" }}>
              <div className="text-[10px] uppercase tracking-[0.18em] mb-2" style={{ color: "var(--color-text-faint)" }}>
                last reply
              </div>
              <div
                className="text-[12px] leading-[1.7]"
                style={{ color: "var(--color-text-dim)", overflowWrap: "anywhere", wordBreak: "break-word" }}
              >
                {clip(turn.reply.text ?? "", 220)}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="overview-ops-card">
        <div className="overview-ops-head">
          <div>
            <div className="text-[10px] uppercase tracking-[0.28em]" style={{ color: "var(--color-text-muted)" }}>
              operations lane
            </div>
            <div className="text-[11px] mt-1" style={{ color: "var(--color-text-faint)" }}>
              attention · tools · model route
            </div>
          </div>
          <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color: attention.length ? "var(--color-amber)" : "var(--color-accent)" }}>
            {attention.length ? `${attention.length} open` : "clear"}
          </div>
        </div>
        <div className="overview-ops-body">
          <SectionKicker label="attention" value={attention.length ? `${attention.length} open` : "clear"} tone={attention.length ? "warn" : "ok"} />
          {attention.length ? (
            <div style={{ marginBottom: 18 }}>
              {attention.map((e, i) => (
                <AttentionRow key={`${e.ts}-${i}`} event={e} />
              ))}
            </div>
          ) : (
            <div style={{ marginBottom: 18 }}>
              <EmptyLine text="no guards, failures, or cooldowns in view" />
            </div>
          )}

          <SectionKicker label="tool mix" value={tools.length ? `${tools.length} active` : "quiet"} />
          {tools.length ? (
            <div style={{ marginBottom: 18 }}>
              {tools.map((t) => <ToolMixRow key={t.name} {...t} />)}
            </div>
          ) : (
            <div style={{ marginBottom: 18 }}>
              <EmptyLine text="no tool calls in the current window" />
            </div>
          )}

          <SectionKicker label="model lane" value={lastModel?.model ? "live" : "empty"} />
          {lastModel ? (
            <div className="text-[11px] leading-[1.7]" style={{ color: "var(--color-text-dim)" }}>
              <div style={{ color: "var(--color-text)", overflowWrap: "anywhere" }}>
                {lastModel.model}
              </div>
              <div className="uppercase tracking-[0.12em]" style={{ color: "var(--color-text-faint)", fontSize: 9 }}>
                {lastModel.host ?? "unknown host"}
              </div>
              <div style={{ marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
                {(lastModel.input_tokens ?? 0).toLocaleString()} in · {(lastModel.output_tokens ?? 0).toLocaleString()} out
                {lastModel.cached_tokens ? ` · ${lastModel.cached_tokens.toLocaleString()} cached` : ""}
              </div>
            </div>
          ) : (
            <EmptyLine text="no model call in the current window" />
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>{label}</div>
      <div className="mt-1 text-[11px] uppercase tracking-[0.08em]" style={{ color: "var(--color-text)", overflowWrap: "anywhere" }}>{value}</div>
    </div>
  );
}

function SectionKicker({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: string;
  tone?: "muted" | "ok" | "warn";
}) {
  const color = tone === "warn"
    ? "var(--color-amber)"
    : tone === "ok"
      ? "var(--color-accent)"
      : "var(--color-text-muted)";
  return (
    <div className="flex items-baseline justify-between gap-4 mb-4">
      <div className="text-[10px] uppercase tracking-[0.28em]" style={{ color: "var(--color-text-muted)" }}>
        ── {label}
      </div>
      <div className="text-[9px] uppercase tracking-[0.18em]" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function MissionStep({ event }: { event: FeedEvent }) {
  const isErr = event.event === "tool_result" && (event.result ?? "").startsWith("ERROR");
  const glyph = event.event === "llm_call" ? "λ"
    : event.event === "tool_call" ? "→"
      : event.event === "tool_result" ? (isErr ? "✗" : "←")
        : event.event === "assistant_reply" ? "›"
          : "·";
  const label = event.event === "tool_call" || event.event === "tool_result"
    ? `${event.event.replace(/_/g, " ")} · ${event.name ?? "tool"}`
    : event.event.replace(/_/g, " ");
  const meta = event.event === "llm_call"
    ? event.model ?? "model"
    : event.event === "tool_result"
      ? statusForResult(event.result)
      : event.service ?? "";
  return (
    <div className="overview-step-row">
      <span style={{ color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums", fontSize: 11 }}>
        {formatTime(event.ts)}
      </span>
      <span style={{ color: isErr ? "var(--color-danger)" : "var(--color-accent)" }}>{glyph}</span>
      <span
        className="uppercase"
        style={{
          color: isErr ? "var(--color-danger)" : "var(--color-text-dim)",
          fontSize: 10,
          letterSpacing: "0.12em",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        {label}
      </span>
      <span
        className="overview-step-meta"
        style={{
          justifySelf: "end",
          color: "var(--color-text-faint)",
          fontSize: 9,
          letterSpacing: "0.08em",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        {meta}
      </span>
    </div>
  );
}

function AttentionRow({ event }: { event: FeedEvent }) {
  const color = event.event === "tool_result" ? "var(--color-danger)" : "var(--color-amber)";
  const label = event.event.replace(/_/g, " ");
  const detail = event.result ?? event.text ?? event.name ?? event.model ?? "";
  return (
    <div style={{ padding: "7px 0", borderBottom: "1px dashed rgba(67,133,105,0.35)" }}>
      <div className="text-[10px] uppercase tracking-[0.14em]" style={{ color }}>
        {label}
      </div>
      <div
        className="text-[11px] leading-[1.55]"
        style={{ color: "var(--color-text-dim)", overflowWrap: "anywhere", wordBreak: "break-word" }}
      >
        {clip(detail, 120)}
      </div>
    </div>
  );
}

function ToolMixRow({ name, count, pct }: { name: string; count: number; pct: number }) {
  return (
    <div className="overview-tool-row">
      <div className="text-[11px]" style={{ color: "var(--color-text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {name}
      </div>
      <div style={{ height: 5, border: "1px solid var(--color-border)", background: "var(--color-surface-2)" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--color-accent)", boxShadow: "0 0 8px var(--color-accent-glow)" }} />
      </div>
      <div className="text-[10px] text-right" style={{ color: "var(--color-accent)", fontVariantNumeric: "tabular-nums" }}>
        {count}
      </div>
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div className="text-[11px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-faint)" }}>
      {text}
    </div>
  );
}

function latestTurn(events: FeedEvent[]) {
  const userIdx = (() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event === "user_message") return i;
    }
    return -1;
  })();
  const from = userIdx >= 0 ? events.slice(userIdx) : events.slice(-10);
  const steps = from
    .filter((e) => ["llm_call", "tool_call", "tool_result", "assistant_reply"].includes(e.event))
    .slice(0, 9);
  return {
    user: userIdx >= 0 ? events[userIdx] : null,
    steps,
    reply: [...from].reverse().find((e) => e.event === "assistant_reply") ?? null,
  };
}

function isAttentionEvent(e: FeedEvent): boolean {
  if (e.event === "tool_result" && (e.result ?? "").startsWith("ERROR")) return true;
  return ["output_guard", "self_correction", "tool_blocked", "budget_blocked", "provider_cooled"].includes(e.event);
}

function toolMix(events: FeedEvent[]) {
  const counts = new Map<string, number>();
  for (const e of events) {
    if (e.event !== "tool_call" || !e.name) continue;
    counts.set(e.name, (counts.get(e.name) ?? 0) + 1);
  }
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  const max = Math.max(1, ...rows.map(([, count]) => count));
  return rows.map(([name, count]) => ({ name, count, pct: Math.max(8, Math.round((count / max) * 100)) }));
}

function statusForResult(result: string | undefined): string {
  if (!result) return "done";
  if (result.startsWith("ERROR")) return "error";
  if (result.length > 240) return `${result.length.toLocaleString()} chars`;
  return "done";
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function clip(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function liveStateMessage(ageSec: number | null): string {
  if (ageSec === null) return "AT REST · NO ACTIVITY";
  if (ageSec < 10) return "● ACTING RIGHT NOW";
  if (ageSec < 60) return `LAST ACTIVITY ${ageSec}s AGO`;
  if (ageSec < 3600) return `LAST ACTIVITY ${Math.floor(ageSec / 60)}m AGO`;
  return `LAST ACTIVITY ${Math.floor(ageSec / 3600)}h AGO`;
}
