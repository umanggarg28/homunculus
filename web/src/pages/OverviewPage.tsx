import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api, parseServerIso } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { SignatureHeartbeat } from "@/components/overview/SignatureHeartbeat";
import { GrowthDeltas } from "@/components/overview/GrowthDeltas";
import { ContextGauge } from "@/components/overview/ContextGauge";
import { HomunculusRobot } from "@/components/robot/HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";
import type { FeedEvent, MemoryEntry, Skill } from "@/lib/types";

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
  const readyItems = useMemo(
    () => buildReadiness({
      lastAgeSec,
      failures: stats.failures,
      activeTasks,
      skills,
      memories,
    }),
    [activeTasks, lastAgeSec, memories, skills, stats.failures],
  );

  return (
    <PageShell>
      <PageHeader title="Overview" subtitle={liveStateMessage(lastAgeSec).toLowerCase()} />
      <div className="overflow-hidden"><SignatureHeartbeat /></div>
      <GrowthDeltas />
      <ContextGauge />
      <style>{`
        .overview-hero-grid {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 320px;
          border: 1px solid var(--color-border);
          background: linear-gradient(180deg, rgba(119,255,61,0.025), transparent), var(--color-surface-1);
        }
        .overview-hero-main {
          border-right: 1px solid var(--color-border);
        }
        .overview-state-grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
        }
        @media (max-width: 980px) {
          .overview-hero-grid {
            grid-template-columns: 1fr;
          }
          .overview-hero-main {
            border-right: none;
            border-bottom: 1px solid var(--color-border);
          }
          .overview-state-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }
        @media (max-width: 560px) {
          .overview-hero-main {
            padding: 20px !important;
          }
          .overview-state-grid {
            grid-template-columns: 1fr;
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
          padding: 18px;
          min-width: 0;
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
            padding: 14px;
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

      {/* ── HERO ROW — countdown + robot panel ── */}
      <div
        className="overview-hero-grid gap-0 mb-10"
      >
          {/* LEFT — countdown as hero + stats */}
          <div className="overview-hero-main p-8 flex flex-col justify-center gap-0">
            <UpcomingHero stats={stats} lastEvent={lastEvent} />
          </div>

          {/* RIGHT — robot panel */}
          <AgentPanel robotState={robotState} cyc={cyc} />
        </div>

        {/* ── READINESS ── */}
        <div className="p-6 mb-10" style={{ border: "1px solid var(--color-border)" }}>
          <div className="text-[10px] uppercase tracking-[0.32em] mb-4" style={{ color: "var(--color-text-muted)" }}>
            ── readiness
          </div>
          <div
            className="overview-state-grid gap-x-8 gap-y-3"
          >
            {readyItems.map((item) => (
              <ReadinessKV key={item.label} {...item} />
            ))}
          </div>
        </div>

        <MissionControl events={events} />
    </PageShell>
  );
}

// ── helpers ─────────────────────────────────────────────────────────


function ReadinessKV({
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
    <div
      className="flex items-baseline justify-between gap-4 py-2"
      style={{ borderBottom: "1px dashed var(--color-border)" }}
    >
      <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </span>
      <span className="flex items-baseline gap-3">
        <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-faint)" }}>
          {hint}
        </span>
        <span
          className="text-[18px]"
          style={{ color, fontVariantNumeric: "tabular-nums", letterSpacing: "0" }}
        >
          {value}
        </span>
      </span>
    </div>
  );
}

function buildReadiness({
  lastAgeSec,
  failures,
  activeTasks,
  skills,
  memories,
}: {
  lastAgeSec: number | null;
  failures: number;
  activeTasks: number;
  skills: Skill[];
  memories: MemoryEntry[];
}) {
  const usedTools = skills.filter((s) => s.call_count > 0).length;
  const failedTools = skills.filter((s) => s.failure_count > 0).length;
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
      value: activeTasks > 0 ? "ARMED" : "EMPTY",
      hint: activeTasks > 0 ? `${activeTasks} active tasks` : "no active tasks",
      tone: activeTasks > 0 ? "ok" : "idle",
    },
    {
      label: "capability",
      value: usedTools > 0 ? `${pad(usedTools)}/${pad(skills.length)}` : "COLD",
      hint: memories.length > 0 ? `${memories.length} memories` : "no memory",
      tone: usedTools > 0 && memories.length > 0 ? "ok" : "idle",
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

function nowNarration(lastEvent: { event: string; name?: string } | undefined): string {
  if (!lastEvent) return "─ no activity yet";
  switch (lastEvent.event) {
    case "llm_call": return "thinking…";
    case "tool_call": return `calling ${(lastEvent.name ?? "tool").toLowerCase()}…`;
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
              style={{
                fontSize: "clamp(54px, 8vw, 104px)",
                lineHeight: 0.85,
                fontVariantNumeric: "tabular-nums",
                letterSpacing: "0",
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
        {nowNarration(lastEvent)}
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

function MissionControl({ events }: { events: FeedEvent[] }) {
  const recent = events.slice(-80);
  const turn = latestTurn(recent);
  const attention = recent
    .filter((e) => isAttentionEvent(e))
    .slice(-4)
    .reverse();
  const tools = toolMix(recent);
  const lastModel = [...recent].reverse().find((e) => e.event === "llm_call");

  return (
    <div className="overview-mission-grid mb-10">
      <div className="overview-ops-card">
        <SectionKicker label="mission trace" value={turn.steps.length ? `${turn.steps.length} steps` : "idle"} />
        {turn.user ? (
          <div style={{ marginBottom: 14 }}>
            <div className="text-[10px] uppercase tracking-[0.18em] mb-2" style={{ color: "var(--color-text-faint)" }}>
              latest instruction
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
          <EmptyLine text="no user turn in the current event window" />
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

      <div className="overview-ops-card">
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
