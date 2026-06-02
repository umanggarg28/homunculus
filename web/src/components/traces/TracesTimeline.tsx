import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";

/**
 * Swimlane timeline — horizontal time axis, 4 lanes (USER / LLM / TOOL / REPLY).
 * Service filter isolates web (chat) from heartbeat (autonomous) activity.
 * Blocks are thin horizontal bars; click for a detail panel.
 */

type LaneKey = "USER" | "LLM" | "TOOL" | "REPLY";

const LANES: { key: LaneKey; label: string; color: string }[] = [
  { key: "USER",  label: "USER",     color: "var(--color-indigo)"  },
  { key: "LLM",   label: "LLM",      color: "var(--color-accent)"  },
  { key: "TOOL",  label: "TOOL",     color: "var(--color-warning)"  },
  { key: "REPLY", label: "REPLY",    color: "var(--color-accent)"  },
];

const WINDOWS = [
  { label: "5M",  ms: 5  * 60 * 1000 },
  { label: "15M", ms: 15 * 60 * 1000 },
  { label: "1H",  ms: 60 * 60 * 1000 },
  { label: "24H", ms: 24 * 60 * 60 * 1000 },
];

type ServiceFilter = "all" | "web" | "heartbeat";

interface Block {
  event: FeedEvent;
  lane: LaneKey;
  startMs: number;
  durationMs: number;
  label: string;
  isError: boolean;
}

function laneFor(e: FeedEvent): LaneKey | null {
  switch (e.event) {
    case "user_message":    return "USER";
    case "llm_call":        return "LLM";
    case "tool_call":
    case "tool_result":     return "TOOL";
    case "assistant_reply": return "REPLY";
    default:                return null;
  }
}

function isErrorResult(e: FeedEvent): boolean {
  return typeof e.result === "string" && /^error|✖|fail/i.test(e.result);
}

function labelFor(e: FeedEvent): string {
  switch (e.event) {
    case "user_message":    return "MSG";
    case "llm_call":        return "LLM";
    case "tool_call":       return (e.name ?? "TOOL").toUpperCase();
    case "tool_result":     return (e.name ?? "RES").toUpperCase();
    case "assistant_reply": return "REPLY";
    default:                return (e.event ?? "EVT").toUpperCase();
  }
}

function buildBlocks(events: FeedEvent[], windowStart: number, now: number, service: ServiceFilter): Block[] {
  const filtered = service === "all" ? events : events.filter((e) => {
    const svc = e.service ?? "";
    if (service === "web") return svc === "web";
    if (service === "heartbeat") return svc === "heartbeat";
    return true;
  });

  const sorted = [...filtered].sort((a, b) =>
    new Date(a.ts).getTime() - new Date(b.ts).getTime(),
  );

  const out: Block[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const e = sorted[i];
    const lane = laneFor(e);
    if (!lane) continue;
    const t = new Date(e.ts).getTime();
    if (t < windowStart - 60_000) continue;

    // Collapse tool_call + tool_result pairs into one block
    if (e.event === "tool_result") {
      const prev = out[out.length - 1];
      if (prev && prev.event.event === "tool_call" && prev.event.name === e.name) {
        prev.durationMs = Math.max(prev.durationMs, t - prev.startMs);
        prev.isError = prev.isError || isErrorResult(e);
        continue;
      }
    }

    const next = sorted[i + 1];
    const tNext = next ? new Date(next.ts).getTime() : now;
    const dur = Math.min(8000, Math.max(100, tNext - t));
    out.push({ event: e, lane, startMs: t, durationMs: dur, label: labelFor(e), isError: isErrorResult(e) });
  }
  return out;
}

function buildTicks(start: number, end: number, windowMs: number) {
  const ticks: { ts: number; label: string }[] = [];
  const step = windowMs / 5;
  for (let i = 0; i <= 5; i++) {
    const ts = start + step * i;
    const ago = Math.max(0, end - ts);
    let label = "now";
    if (ago > 86_400_000)   label = `${Math.floor(ago / 86_400_000)}D AGO`;
    else if (ago > 3_600_000) label = `${Math.floor(ago / 3_600_000)}H AGO`;
    else if (ago > 60_000)    label = `${Math.floor(ago / 60_000)}M AGO`;
    else if (ago > 1000)      label = `${Math.floor(ago / 1000)}S AGO`;
    ticks.push({ ts, label });
  }
  return ticks;
}

export function TracesTimeline() {
  const { events, connected } = useEventStream(500);
  const [windowMs, setWindowMs]     = useState(WINDOWS[1].ms); // default 15M
  const [now, setNow]               = useState(() => Date.now());
  const [selected, setSelected]     = useState<Block | null>(null);
  const [paused, setPaused]         = useState(false);
  const [service, setService]       = useState<ServiceFilter>("all"); // default: all services
  const [hiddenLanes, setHiddenLanes] = useState<Set<LaneKey>>(new Set());
  const containerRef = useRef<HTMLDivElement>(null);

  const toggleLane = (key: LaneKey) => {
    setHiddenLanes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [paused]);

  const windowStart = now - windowMs;
  const allBlocks = useMemo(
    () => buildBlocks(events, windowStart, now, service),
    [events, windowStart, now, service],
  );
  const blocks = hiddenLanes.size === 0
    ? allBlocks
    : allBlocks.filter((b) => !hiddenLanes.has(b.lane));
  const ticks = useMemo(() => buildTicks(windowStart, now, windowMs), [windowStart, now, windowMs]);

  const laneH  = 48;
  const barH   = 10;
  const rulerH = 26;
  const totalH = rulerH + LANES.length * laneH;

  const xPct = (ts: number) => {
    const p = ((ts - windowStart) / windowMs) * 100;
    return Math.min(100, Math.max(0, p));
  };

  // Count blocks per lane for the header (use allBlocks so counts don't change when lane is hidden)
  const counts = useMemo(() => {
    const c: Record<LaneKey, number> = { USER: 0, LLM: 0, TOOL: 0, REPLY: 0 };
    for (const b of allBlocks) if (b.startMs >= windowStart) c[b.lane]++;
    return c;
  }, [allBlocks, windowStart]);

  return (
    <div className="traces-timeline">
      {/* Controls row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 12, flexWrap: "wrap", fontFamily: "var(--font-mono)" }}>

        {/* Left: window + pause */}
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className="brut-meta" style={{ color: "var(--color-text-muted)", marginRight: 4 }}>WINDOW</span>
          {WINDOWS.map((w) => {
            const active = w.ms === windowMs;
            return (
              <button key={w.label} onClick={() => setWindowMs(w.ms)} className="brut-meta" style={{
                padding: "4px 9px",
                border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
                background: active ? "var(--color-accent)" : "transparent",
                color: active ? "var(--color-bg)" : "var(--color-text-dim)",
                cursor: "pointer", fontFamily: "var(--font-mono)",
              }}>{w.label}</button>
            );
          })}
          <button onClick={() => setPaused((p) => !p)} className="brut-meta" style={{
            padding: "4px 9px", marginLeft: 6,
            border: `1px solid ${paused ? "var(--color-warning)" : "var(--color-border)"}`,
            background: paused ? "var(--color-warning)" : "transparent",
            color: paused ? "var(--color-bg)" : "var(--color-text-muted)",
            cursor: "pointer", fontFamily: "var(--font-mono)",
          }}>{paused ? "▶ RESUME" : "⏸ PAUSE"}</button>
        </div>

        {/* Right: service filter + live + lane counts */}
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {/* Service filter */}
          <div style={{ display: "flex", gap: 0, border: "1px solid var(--color-border)" }}>
            {(["web", "heartbeat", "all"] as ServiceFilter[]).map((s) => {
              const active = service === s;
              return (
                <button key={s} onClick={() => setService(s)} style={{
                  padding: "3px 8px",
                  border: "none",
                  borderRight: s !== "all" ? "1px solid var(--color-border)" : "none",
                  background: active ? "var(--color-border-strong)" : "transparent",
                  color: active ? "var(--color-text)" : "var(--color-text-faint)",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                }}>{s}</button>
              );
            })}
          </div>

          {/* Lane counts — click to toggle lane visibility */}
          <div className="brut-meta" style={{ display: "flex", gap: 6, color: "var(--color-text-muted)" }}>
            {LANES.map((l) => {
              const hidden = hiddenLanes.has(l.key);
              return (
                <button key={l.key} onClick={() => toggleLane(l.key)} title={hidden ? `show ${l.key}` : `hide ${l.key}`} style={{
                  background: "none", border: `1px solid ${hidden ? "var(--color-border)" : l.color}`,
                  padding: "2px 7px", cursor: "pointer", fontFamily: "var(--font-mono)",
                  fontSize: 9, letterSpacing: "0.14em",
                  color: hidden ? "var(--color-text-faint)" : l.color,
                  opacity: hidden ? 0.4 : 1,
                }}>
                  {l.key} <span style={{ color: hidden ? "var(--color-text-faint)" : "var(--color-text-faint)" }}>{counts[l.key].toString().padStart(2, "0")}</span>
                </button>
              );
            })}
          </div>

          {/* Live indicator */}
          <span style={{
            color: connected ? "var(--color-accent)" : "var(--color-warning)",
            fontSize: 10, letterSpacing: "0.16em",
            textShadow: connected ? "0 0 6px var(--color-accent-glow)" : "none",
          }}>● {connected ? "LIVE" : "OFFLINE"}</span>
        </div>
      </div>

      <div
        className="traces-chart-grid"
        style={{ gridTemplateColumns: selected ? "minmax(0, 1fr) 340px" : "minmax(0, 1fr)" }}
      >
        {/* Chart */}
        <div ref={containerRef} style={{
          border: "1px solid var(--color-border)",
          background: "var(--color-surface-1)",
          position: "relative", height: totalH, overflow: "hidden",
        }}>
          {/* Ruler */}
          <div style={{
            position: "absolute", left: 0, right: 0, top: 0, height: rulerH,
            borderBottom: "1px solid var(--color-border)",
            background: "rgba(0,0,0,0.3)",
          }}>
            {ticks.map((tk, i) => {
              const isLast = i === ticks.length - 1;
              return (
              <div key={i} style={{
                position: "absolute",
                left: isLast ? "auto" : `${xPct(tk.ts)}%`,
                right: isLast ? 6 : "auto",
                top: 0,
                bottom: 0,
                borderLeft: isLast ? "none" : "1px solid var(--color-border)",
                borderRight: isLast ? "1px solid var(--color-border)" : "none",
                paddingLeft: 5, paddingTop: 6,
                fontSize: 9, letterSpacing: "0.14em",
                color: "var(--color-text-faint)", fontFamily: "var(--font-mono)",
              }}>{tk.label}</div>
            )})}
          </div>

          {/* Lane backgrounds + labels */}
          {LANES.map((lane, i) => (
            <div key={lane.key} style={{
              position: "absolute", left: 0, right: 0,
              top: rulerH + i * laneH, height: laneH,
              borderBottom: i < LANES.length - 1 ? "1px dashed rgba(255,255,255,0.04)" : "none",
            }}>
              <div style={{
                position: "absolute", left: 10, top: 7,
                fontSize: 8, letterSpacing: "0.22em",
                color: lane.color, fontFamily: "var(--font-mono)",
                fontWeight: 700, textTransform: "uppercase", opacity: 0.4,
                pointerEvents: "none",
              }}>── {lane.label}</div>
            </div>
          ))}

          {/* Now line */}
          <div style={{
            position: "absolute", left: `${xPct(now)}%`, top: rulerH, bottom: 0,
            borderLeft: "1px solid var(--color-accent)",
            boxShadow: "0 0 8px var(--color-accent-glow)",
            pointerEvents: "none",
          }} />

          {/* Blocks */}
          {blocks.map((b, i) => {
            const laneIdx = LANES.findIndex((l) => l.key === b.lane);
            const top = rulerH + laneIdx * laneH + (laneH - barH) / 2;
            const left = xPct(b.startMs);
            const widthPct = (b.durationMs / windowMs) * 100;
            const isSelected = selected?.event === b.event;
            const color = b.isError ? "var(--color-danger)" : LANES[laneIdx].color;

            return (
              <button key={i} onClick={() => setSelected(isSelected ? null : b)} style={{
                position: "absolute",
                left: `${left}%`,
                width: `max(${widthPct}%, 5px)`,
                top, height: barH,
                background: isSelected ? color : `color-mix(in srgb, ${color} 30%, transparent)`,
                border: `1px solid ${color}`,
                boxShadow: isSelected ? `0 0 8px ${color}` : "none",
                borderRadius: 2,
                padding: 0,
                cursor: "pointer",
                overflow: "hidden",
              }}
              title={`${b.label}${b.event.name ? ` · ${b.event.name}` : ""} · ${b.durationMs < 1000 ? `${b.durationMs}ms` : `${(b.durationMs/1000).toFixed(1)}s`} · ${new Date(b.startMs).toLocaleTimeString()}`}
              />
            );
          })}

          {/* Empty state */}
          {blocks.length === 0 && (
            <EmptyState
              events={events}
              windowStart={windowStart}
              windowMs={windowMs}
              service={service}
              rulerH={rulerH}
              onWiden={() => setWindowMs(WINDOWS[WINDOWS.length - 1].ms)}
              onShowAll={() => setService("all")}
            />
          )}
        </div>

        {/* Detail panel */}
        {selected && <DetailPanel block={selected} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}

function EmptyState({ events, windowStart, windowMs, service, rulerH, onWiden, onShowAll }: {
  events: FeedEvent[]; windowStart: number; windowMs: number;
  service: ServiceFilter; rulerH: number;
  onWiden: () => void; onShowAll: () => void;
}) {
  let latest: FeedEvent | null = null;
  let latestTs = -Infinity;
  for (const e of events) {
    if (!laneFor(e)) continue;
    const t = new Date(e.ts).getTime();
    if (t > latestTs) { latestTs = t; latest = e; }
  }

  const hasOlderEvents = latest !== null && latestTs < windowStart;
  const windowLabel = windowMs <= 5*60*1000 ? "5 minutes" : windowMs <= 15*60*1000 ? "15 minutes" : windowMs <= 3600*1000 ? "1 hour" : "24 hours";
  const ageMs = latest ? Date.now() - latestTs : null;
  const ageLabel = ageMs === null ? null : ageMs < 60000 ? `${Math.floor(ageMs/1000)}s ago` : ageMs < 3600000 ? `${Math.floor(ageMs/60000)}m ago` : `${Math.floor(ageMs/3600000)}h ago`;

  return (
    <div style={{
      position: "absolute", left: 0, right: 0, top: rulerH, bottom: 0,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 10, color: "var(--color-text-faint)", fontFamily: "var(--font-mono)", textAlign: "center", padding: 24,
    }}>
      <div style={{ fontSize: 10, letterSpacing: "0.18em", textTransform: "uppercase" }}>
        ── no {service !== "all" ? service + " " : ""}events in last {windowLabel}
      </div>
      {hasOlderEvents && (
        <>
          <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
            last activity <span style={{ color: "var(--color-accent)" }}>{ageLabel}</span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <ActionBtn onClick={onWiden}>[ widen to 24h ]</ActionBtn>
            {service !== "all" && <ActionBtn onClick={onShowAll}>[ show all services ]</ActionBtn>}
          </div>
        </>
      )}
      {!latest && (
        <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>talk to the agent to populate</div>
      )}
    </div>
  );
}

function ActionBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      border: "1px solid var(--color-accent)", background: "transparent",
      color: "var(--color-accent)", padding: "5px 10px",
      fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.16em",
      textTransform: "uppercase", cursor: "pointer",
    }}>{children}</button>
  );
}

function DetailPanel({ block, onClose }: { block: Block; onClose: () => void }) {
  const e = block.event;
  const dur = block.durationMs < 1000 ? `${block.durationMs}ms` : `${(block.durationMs/1000).toFixed(1)}s`;

  return (
    <div style={{
      border: "1px solid var(--color-border)",
      background: "var(--color-surface-1)",
      fontFamily: "var(--font-mono)", padding: 16,
      maxHeight: "calc(100vh - 200px)", overflowY: "auto",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <span style={{ fontSize: 9, letterSpacing: "0.20em", color: "var(--color-accent)" }}>
          ── {block.label}
        </span>
        <button onClick={onClose} style={{
          border: "1px solid var(--color-border)", padding: "3px 7px",
          background: "transparent", color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)", fontSize: 10, cursor: "pointer",
        }}>[✕]</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "70px 1fr", rowGap: 5, columnGap: 10, marginBottom: 14 }}>
        <KV k="event"    v={e.event} />
        <KV k="service"  v={e.service ?? "—"} />
        <KV k="time"     v={new Date(block.startMs).toLocaleTimeString()} />
        <KV k="duration" v={dur} />
        {e.name  && <KV k="name"  v={e.name} />}
        {e.model && <KV k="model" v={e.model} />}
        {e.host  && <KV k="host"  v={e.host} />}
      </div>

      {e.text   && <TextBlock label="TEXT"   text={e.text} />}
      {e.args   && <TextBlock label="ARGS"   text={e.args} />}
      {e.result && <TextBlock label="RESULT" text={e.result} />}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <span style={{ fontSize: 9, letterSpacing: "0.18em", color: "var(--color-text-faint)", textTransform: "uppercase" }}>{k}</span>
      <span style={{ fontSize: 11, color: "var(--color-text-dim)", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>{v}</span>
    </>
  );
}

function TextBlock({ label, text }: { label: string; text: string }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 9, letterSpacing: "0.18em", color: "var(--color-text-faint)", marginBottom: 4, textTransform: "uppercase" }}>
        ── {label}
      </div>
      <pre style={{
        margin: 0, padding: 10,
        background: "rgba(0,0,0,0.45)", border: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--color-text)",
        whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 220, overflow: "auto",
      }}>{text}</pre>
    </div>
  );
}
