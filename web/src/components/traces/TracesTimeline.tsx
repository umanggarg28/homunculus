import { useEffect, useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";
import { Tooltip } from "@/components/ui/Tooltip";

/**
 * Activity grid — horizontal time axis, 4 lanes (USER / LLM / TOOL /
 * REPLY), each lane a row of density cells (one per time bucket).
 *
 * Why cells, not duration bars: events here are seconds long. On a 24h
 * window an 8s bar is 0.009% wide — the old Gantt render drew the lanes
 * empty exactly when the user zoomed out to "what happened today?".
 * Density cells are zoom-invariant: a bucket with activity is visible
 * at every window size, brightness encodes how much, red encodes
 * errors. (It also reads more dot-matrix-CRT than bars ever did.)
 *
 * Click a cell to open the bucket inspector listing its events; click
 * an event to expand the full record. Service filter isolates web
 * (chat) from heartbeat (autonomous) activity.
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

/** Buckets across the window. 72 keeps cells chunky enough to click
 * (≈13px at 960px wide) while resolving 20-minute structure on 24H. */
const BUCKETS = 72;

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
    if (t < windowStart) continue;

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

/** One lane-row of the grid: per-bucket event lists + max count. */
interface LaneBuckets {
  lane: LaneKey;
  cells: Block[][];          // BUCKETS entries
  max: number;               // busiest cell in THIS lane (for scaling)
}

function bucketize(blocks: Block[], windowStart: number, windowMs: number): LaneBuckets[] {
  const perLane = new Map<LaneKey, Block[][]>(
    LANES.map((l) => [l.key, Array.from({ length: BUCKETS }, () => [] as Block[])]),
  );
  const bucketMs = windowMs / BUCKETS;
  for (const b of blocks) {
    const idx = Math.min(BUCKETS - 1, Math.max(0, Math.floor((b.startMs - windowStart) / bucketMs)));
    perLane.get(b.lane)!![idx].push(b);
  }
  return LANES.map((l) => {
    const cells = perLane.get(l.key)!;
    return { lane: l.key, cells, max: Math.max(1, ...cells.map((c) => c.length)) };
  });
}

interface Selection { lane: LaneKey; bucket: number }

interface TimelineProps {
  /** Epoch ms to center attention on (e.g. a task run deep link).
   *  Picks the smallest window containing it and draws a marker. */
  focusTs?: number;
}

export function TracesTimeline({ focusTs }: TimelineProps = {}) {
  const { events, connected } = useEventStream(500);
  const [windowMs, setWindowMs]     = useState(() => {
    if (focusTs && Number.isFinite(focusTs)) {
      const age = Date.now() - focusTs;
      const fit = WINDOWS.find((w) => w.ms > age * 1.1);
      return (fit ?? WINDOWS[WINDOWS.length - 1]).ms;
    }
    return WINDOWS[1].ms; // default 15M
  });
  const [now, setNow]               = useState(() => Date.now());
  const [selected, setSelected]     = useState<Selection | null>(null);
  const [paused, setPaused]         = useState(false);
  const [service, setService]       = useState<ServiceFilter>("all");
  const [hiddenLanes, setHiddenLanes] = useState<Set<LaneKey>>(new Set());
  // Deep links already chose their window — auto-widen would fight it.
  const [autoWidened, setAutoWidened] = useState(() => Boolean(focusTs));

  const toggleLane = (key: LaneKey) => {
    setHiddenLanes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // Auto-widen the window on first render IF the default (15M) contains no
  // real activity. Picks the smallest WINDOWS option that actually has
  // events with a lane. Skipped once the user touches WINDOW manually.
  useEffect(() => {
    if (autoWidened || events.length === 0) return;
    const nowMs = Date.now();
    const hasLane = (e: FeedEvent) => laneFor(e) !== null;
    const inWindow = (e: FeedEvent, ms: number) => new Date(e.ts).getTime() >= nowMs - ms;
    if (events.some((e) => hasLane(e) && inWindow(e, windowMs))) {
      setAutoWidened(true);
      return;
    }
    for (const w of WINDOWS) {
      if (w.ms <= windowMs) continue;
      if (events.some((e) => hasLane(e) && inWindow(e, w.ms))) {
        setWindowMs(w.ms);
        setAutoWidened(true);
        return;
      }
    }
    setAutoWidened(true);
  }, [events, windowMs, autoWidened]);

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
  const laneBuckets = useMemo(
    () => bucketize(allBlocks, windowStart, windowMs),
    [allBlocks, windowStart, windowMs],
  );
  const ticks = useMemo(() => buildTicks(windowStart, now, windowMs), [windowStart, now, windowMs]);

  const laneH  = 48;
  const rulerH = 26;
  const totalH = rulerH + LANES.length * laneH;
  const bucketMs = windowMs / BUCKETS;

  const xPct = (ts: number) => {
    const p = ((ts - windowStart) / windowMs) * 100;
    return Math.min(100, Math.max(0, p));
  };

  const counts = useMemo(() => {
    const c: Record<LaneKey, number> = { USER: 0, LLM: 0, TOOL: 0, REPLY: 0 };
    for (const b of allBlocks) c[b.lane]++;
    return c;
  }, [allBlocks]);

  const traceSummary = useMemo(() => {
    const errors = allBlocks.filter((b) => b.isError).length;
    const tools = allBlocks.filter((b) => b.lane === "TOOL").length;
    const models = allBlocks.filter((b) => b.lane === "LLM").length;
    const latest = allBlocks[allBlocks.length - 1];
    const latestLabel = latest
      ? `${latest.label}${latest.event.name ? ` · ${latest.event.name}` : ""}`
      : "no lane events";
    return { total: allBlocks.length, errors, tools, models, latestLabel };
  }, [allBlocks]);

  const selectedBlocks = useMemo(() => {
    if (!selected) return null;
    const laneRow = laneBuckets.find((l) => l.lane === selected.lane);
    return laneRow ? laneRow.cells[selected.bucket] : null;
  }, [selected, laneBuckets]);

  const anyActivity = allBlocks.length > 0;

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
              <button key={w.label} onClick={() => { setWindowMs(w.ms); setAutoWidened(true); setSelected(null); }} className="brut-meta" style={{
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

        {/* Right: service filter + lane toggles + live */}
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div style={{ display: "flex", gap: 0, border: "1px solid var(--color-border)" }}>
            {(["web", "heartbeat", "all"] as ServiceFilter[]).map((s) => {
              const active = service === s;
              return (
                <button key={s} onClick={() => { setService(s); setSelected(null); }} style={{
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

          <div className="brut-meta" style={{ display: "flex", gap: 6, color: "var(--color-text-muted)" }}>
            {LANES.map((l) => {
              const hidden = hiddenLanes.has(l.key);
              return (
                <Tooltip key={l.key} text={hidden ? `show ${l.key}` : `hide ${l.key}`} placement="top">
                <button onClick={() => toggleLane(l.key)} style={{
                  background: "none", border: `1px solid ${hidden ? "var(--color-border)" : l.color}`,
                  padding: "2px 7px", cursor: "pointer", fontFamily: "var(--font-mono)",
                  fontSize: 9, letterSpacing: "0.14em",
                  color: hidden ? "var(--color-text-faint)" : l.color,
                  opacity: hidden ? 0.4 : 1,
                }}>
                  {l.key} <span style={{ color: "var(--color-text-faint)" }}>{counts[l.key].toString().padStart(2, "0")}</span>
                </button>
                </Tooltip>
              );
            })}
          </div>

          <span style={{
            color: connected ? "var(--color-accent)" : "var(--color-warning)",
            fontSize: 10, letterSpacing: "0.16em",
            textShadow: connected ? "0 0 6px var(--color-accent-glow)" : "none",
          }}>● {connected ? "LIVE" : "OFFLINE"}</span>
        </div>
      </div>

      <div
        className="traces-summary-strip instrument-panel hm-panel-scan hm-panel-secondary mb-3 px-4 py-3"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: 14,
          fontFamily: "var(--font-mono)",
        }}
      >
        <TraceMetric label="window events" value={traceSummary.total.toString().padStart(2, "0")} />
        <TraceMetric label="model calls" value={traceSummary.models.toString().padStart(2, "0")} tone="accent" />
        <TraceMetric label="tool spans" value={traceSummary.tools.toString().padStart(2, "0")} tone="amber" />
        <TraceMetric label={traceSummary.errors > 0 ? "errors" : "latest"} value={traceSummary.errors > 0 ? traceSummary.errors.toString().padStart(2, "0") : traceSummary.latestLabel} tone={traceSummary.errors > 0 ? "danger" : "muted"} />
      </div>

      <div
        className="traces-chart-grid"
        style={{ gridTemplateColumns: selectedBlocks ? "minmax(0, 1fr) 340px" : "minmax(0, 1fr)" }}
      >
        {/* Activity grid */}
        <div className="instrument-panel hm-panel-scan hm-panel-primary hm-traces-frame" style={{
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

          {/* Lane rows */}
          {laneBuckets.map((row, laneIdx) => {
            const laneMeta = LANES[laneIdx];
            const hidden = hiddenLanes.has(row.lane);
            return (
              <div key={row.lane} style={{
                position: "absolute", left: 0, right: 0,
                top: rulerH + laneIdx * laneH, height: laneH,
                borderBottom: laneIdx < LANES.length - 1 ? "1px dashed rgba(255,255,255,0.04)" : "none",
              }}>
                <div style={{
                  position: "absolute", left: 10, top: 7,
                  fontSize: 8, letterSpacing: "0.22em",
                  color: laneMeta.color, fontFamily: "var(--font-mono)",
                  fontWeight: 700, textTransform: "uppercase", opacity: 0.4,
                  pointerEvents: "none", zIndex: 2,
                }}>── {laneMeta.label}</div>

                {!hidden && row.cells.map((cell, i) => {
                  if (cell.length === 0) return null;
                  const hasError = cell.some((b) => b.isError);
                  const color = hasError ? "var(--color-danger)" : laneMeta.color;
                  // sqrt scale: one event is clearly visible, the
                  // busiest cell saturates instead of flattening the rest.
                  const intensity = Math.sqrt(cell.length / row.max);
                  const fillPct = Math.round(22 + 68 * intensity);
                  const isSel = selected?.lane === row.lane && selected.bucket === i;
                  const t0 = windowStart + i * bucketMs;
                  return (
                    <button
                      key={i}
                      onClick={() => setSelected(isSel ? null : { lane: row.lane, bucket: i })}
                      aria-label={`${cell.length} event${cell.length > 1 ? "s" : ""}${hasError ? " · errors" : ""} · ${new Date(t0).toLocaleTimeString()}`}
                      style={{
                        position: "absolute",
                        left: `${(i / BUCKETS) * 100}%`,
                        width: `${100 / BUCKETS}%`,
                        top: 14, bottom: 8,
                        padding: 0,
                        cursor: "pointer",
                        border: isSel ? `1px solid ${color}` : "1px solid transparent",
                        background: `color-mix(in srgb, ${color} ${fillPct}%, transparent)`,
                        boxShadow: isSel
                          ? `0 0 10px ${color}`
                          : hasError
                            ? `0 0 6px color-mix(in srgb, ${color} 55%, transparent)`
                            : "none",
                        borderRadius: 1,
                      }}
                    />
                  );
                })}
              </div>
            );
          })}

          {/* Focus marker — the moment a deep link asked about (amber
              dashed, vs the solid accent now-line). */}
          {focusTs != null && focusTs >= windowStart && focusTs <= now && (
            <div style={{
              position: "absolute", left: `${xPct(focusTs)}%`, top: rulerH, bottom: 0,
              borderLeft: "1px dashed var(--color-warning)",
              pointerEvents: "none",
            }}>
              <span style={{
                position: "absolute", top: 2, left: 4,
                fontSize: 8, letterSpacing: "0.18em", fontFamily: "var(--font-mono)",
                color: "var(--color-warning)", textTransform: "uppercase",
              }}>run</span>
            </div>
          )}

          {/* Now line */}
          <div style={{
            position: "absolute", left: `${xPct(now)}%`, top: rulerH, bottom: 0,
            borderLeft: "1px solid var(--color-accent)",
            boxShadow: "0 0 8px var(--color-accent-glow)",
            pointerEvents: "none",
          }} />

          {!anyActivity && (
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

        {/* Bucket inspector */}
        {selectedBlocks && selected && (
          <BucketPanel
            blocks={selectedBlocks}
            lane={selected.lane}
            from={windowStart + selected.bucket * bucketMs}
            to={windowStart + (selected.bucket + 1) * bucketMs}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}

function TraceMetric({ label, value, tone = "muted" }: { label: string; value: string; tone?: "accent" | "amber" | "danger" | "muted" }) {
  const color = tone === "accent"
    ? "var(--color-accent)"
    : tone === "amber"
      ? "var(--color-amber)"
      : tone === "danger"
        ? "var(--color-danger)"
        : "var(--color-text-dim)";
  return (
    <div style={{ minWidth: 0 }}>
      <div className="brut-meta" style={{ color: "var(--color-text-faint)" }}>{label}</div>
      <Tooltip text={value} placement="top">
      <div
        className="truncate"
        style={{
          color,
          fontSize: 13,
          lineHeight: 1.4,
          marginTop: 3,
          letterSpacing: label === "latest" ? "0.04em" : "0",
          fontVariantNumeric: "tabular-nums",
          textTransform: label === "latest" ? "uppercase" : "none",
        }}
      >
        {value}
      </div>
      </Tooltip>
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

/** Bucket inspector — the events inside one clicked cell. One line per
 * event; click a line to expand the full record (args/result/text). */
function BucketPanel({ blocks, lane, from, to, onClose }: {
  blocks: Block[]; lane: LaneKey; from: number; to: number; onClose: () => void;
}) {
  const [openIdx, setOpenIdx] = useState<number | null>(blocks.length === 1 ? 0 : null);
  const laneColor = LANES.find((l) => l.key === lane)?.color ?? "var(--color-accent)";
  const fmt = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary hm-traces-frame" style={{
      background: "var(--color-surface-1)",
      fontFamily: "var(--font-mono)", padding: 16,
      maxHeight: "calc(100vh - 200px)", overflowY: "auto",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 14, marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 9, letterSpacing: "0.20em", color: "var(--color-text-faint)" }}>
            ── bucket · {fmt(from)}–{fmt(to)}
          </div>
          <div style={{ marginTop: 4, fontSize: 15, lineHeight: 1.2, color: laneColor, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            {lane} · {blocks.length} event{blocks.length > 1 ? "s" : ""}
          </div>
        </div>
        <button onClick={onClose} style={{
          border: "1px solid var(--color-border)", padding: "3px 7px",
          background: "transparent", color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)", fontSize: 10, cursor: "pointer",
        }}>[✕]</button>
      </div>

      {blocks.map((b, i) => {
        const open = openIdx === i;
        const color = b.isError ? "var(--color-danger)" : laneColor;
        const dur = b.durationMs < 1000 ? `${b.durationMs}ms` : `${(b.durationMs / 1000).toFixed(1)}s`;
        const e = b.event;
        return (
          <div key={i} style={{ borderTop: "1px solid var(--color-border)" }}>
            <button
              onClick={() => setOpenIdx(open ? null : i)}
              style={{
                display: "flex", width: "100%", gap: 8, alignItems: "baseline",
                background: open ? "rgba(255,255,255,0.03)" : "transparent",
                border: "none", padding: "7px 2px", cursor: "pointer",
                fontFamily: "var(--font-mono)", textAlign: "left",
              }}
            >
              <span style={{ fontSize: 10, color: "var(--color-text-faint)", flexShrink: 0 }}>{fmt(b.startMs)}</span>
              <span className="truncate" style={{ fontSize: 11, color, textTransform: "uppercase", letterSpacing: "0.05em", minWidth: 0 }}>
                {b.isError ? "✖ " : ""}{b.label}
              </span>
              <span style={{ fontSize: 9, color: "var(--color-text-faint)", marginLeft: "auto", flexShrink: 0 }}>{dur} {open ? "▾" : "▸"}</span>
            </button>
            {open && (
              <div style={{ padding: "2px 2px 10px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "70px 1fr", rowGap: 5, columnGap: 10, marginBottom: 10 }}>
                  <KV k="event"    v={e.event} />
                  <KV k="service"  v={e.service ?? "—"} />
                  {e.name  && <KV k="name"  v={e.name} />}
                  {e.model && <KV k="model" v={e.model} />}
                  {e.host  && <KV k="host"  v={e.host} />}
                </div>
                {e.text   && <TextBlock label="TEXT"   text={e.text} />}
                {e.args   && <TextBlock label="ARGS"   text={e.args} />}
                {e.result && <TextBlock label="RESULT" text={e.result} />}
              </div>
            )}
          </div>
        );
      })}
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
