import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";

/** Gantt-style timeline for the agent's activity stream.
 *
 *  Horizontal time axis (newest right), 5 swimlanes:
 *      USER   — user_message
 *      LLM    — llm_call
 *      TOOL   — tool_call + tool_result (collapsed)
 *      MEMORY — memory_*  events
 *      REPLY  — assistant_reply
 *
 *  Each event becomes a hairline-bordered block. Block width = ms
 *  until the next chronological event (capped at a sensible max).
 *  Click a block to inspect the full record in a side panel.
 *
 *  Window picker: 5m / 15m / 1h / 24h (relative to "now"). The
 *  whole thing keeps scrolling forward in real time. */

type LaneKey = "USER" | "LLM" | "TOOL" | "MEMORY" | "REPLY";
const LANES: { key: LaneKey; label: string; color: string }[] = [
  { key: "USER",   label: "USER",       color: "var(--color-indigo)"  },
  { key: "LLM",    label: "LLM CALL",   color: "var(--color-accent)"  },
  { key: "TOOL",   label: "TOOL",       color: "var(--color-warning)" },
  { key: "MEMORY", label: "MEMORY",     color: "var(--color-info)"    },
  { key: "REPLY",  label: "REPLY",      color: "var(--color-accent)"  },
];

const WINDOWS = [
  { label: "5M",  ms: 5 * 60 * 1000 },
  { label: "15M", ms: 15 * 60 * 1000 },
  { label: "1H",  ms: 60 * 60 * 1000 },
  { label: "24H", ms: 24 * 60 * 60 * 1000 },
];

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
    default:
      if (typeof e.event === "string" && e.event.startsWith("memory")) return "MEMORY";
      return null;
  }
}

function buildBlocks(events: FeedEvent[], windowStart: number, now: number): Block[] {
  const sorted = [...events].sort((a, b) =>
    new Date(a.ts).getTime() - new Date(b.ts).getTime(),
  );
  const out: Block[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const e = sorted[i];
    const lane = laneFor(e);
    if (!lane) continue;
    const t = new Date(e.ts).getTime();
    if (t < windowStart - 60_000) continue;
    // Duration = time to next chronological event, capped at 5s (so a
    // single LLM call doesn't dominate the lane). For paired tool_call →
    // tool_result events, we collapse into one block.
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
    const dur = Math.min(8000, Math.max(120, tNext - t));
    const isError =
      e.event === "tool_result" && isErrorResult(e);
    const label = labelFor(e);
    out.push({ event: e, lane, startMs: t, durationMs: dur, label, isError });
  }
  return out;
}

function isErrorResult(e: FeedEvent): boolean {
  return typeof e.result === "string" && /^error|✖|fail/i.test(e.result);
}

function labelFor(e: FeedEvent): string {
  // Short, semantic labels (3-12 chars). Block widths shrink hard at
  // the 1H/24H zoom levels so anything longer just gets clipped to
  // a meaningless first letter. Full content lives in the detail panel.
  switch (e.event) {
    case "user_message":    return "USER";
    case "llm_call":        return "LLM";
    case "tool_call":       return (e.name ?? "TOOL").toUpperCase();
    case "tool_result":     return (e.name ?? "RESULT").toUpperCase();
    case "assistant_reply": return "REPLY";
    default:                return (e.event ?? "EVENT").toUpperCase();
  }
}

export function TracesTimeline() {
  const { events, connected } = useEventStream(500);
  const [windowMs, setWindowMs] = useState(WINDOWS[1].ms);
  const [now, setNow] = useState(() => Date.now());
  const [selected, setSelected] = useState<Block | null>(null);
  const [paused, setPaused] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [paused]);

  const windowStart = now - windowMs;
  const blocks = useMemo(
    () => buildBlocks(events, windowStart, now),
    [events, windowStart, now],
  );

  const laneCount = LANES.length;
  const laneH = 56;
  const rulerH = 28;
  const totalH = rulerH + laneCount * laneH;

  // Convert a timestamp to a percent across the window.
  const xPct = (ts: number) => {
    const p = ((ts - windowStart) / windowMs) * 100;
    return Math.min(100, Math.max(0, p));
  };

  const ticks = useMemo(() => buildTicks(windowStart, now, windowMs), [windowStart, now, windowMs]);

  const counts = useMemo(() => {
    const c: Record<LaneKey, number> = { USER: 0, LLM: 0, TOOL: 0, MEMORY: 0, REPLY: 0 };
    for (const b of blocks) c[b.lane]++;
    return c;
  }, [blocks]);

  return (
    <div>
      {/* Header strip — window selector + connection status + counts */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
            WINDOW
          </span>
          {WINDOWS.map((w) => {
            const active = w.ms === windowMs;
            return (
              <button
                key={w.label}
                onClick={() => setWindowMs(w.ms)}
                className="brut-meta"
                style={{
                  padding: "5px 10px",
                  border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
                  background: active ? "var(--color-accent)" : "transparent",
                  color: active ? "var(--color-bg)" : "var(--color-text-dim)",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {w.label}
              </button>
            );
          })}
          <button
            onClick={() => setPaused((p) => !p)}
            className="brut-meta"
            style={{
              padding: "5px 10px",
              border: `1px solid ${paused ? "var(--color-warning)" : "var(--color-border)"}`,
              background: paused ? "var(--color-warning)" : "transparent",
              color: paused ? "var(--color-bg)" : "var(--color-text-muted)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              marginLeft: 8,
            }}
          >
            {paused ? "▶ RESUME" : "⏸ PAUSE"}
          </button>
        </div>

        <div className="brut-meta" style={{ color: "var(--color-text-muted)", display: "flex", gap: 14 }}>
          {LANES.map((l) => (
            <span key={l.key} style={{ color: l.color }}>
              {l.key} <span style={{ color: "var(--color-text-faint)" }}>{counts[l.key].toString().padStart(2, "0")}</span>
            </span>
          ))}
          <span
            style={{
              color: connected ? "var(--color-accent)" : "var(--color-warning)",
              textShadow: connected ? "0 0 6px var(--color-accent-glow)" : "none",
            }}
          >
            ● {connected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 360px" : "1fr", gap: 16 }}>
        <div
          ref={containerRef}
          style={{
            border: "1px solid var(--color-border)",
            background: "var(--color-surface-1)",
            position: "relative",
            height: totalH,
            overflow: "hidden",
          }}
        >
          {/* Time ruler */}
          <div
            style={{
              position: "absolute",
              left: 0, right: 0, top: 0,
              height: rulerH,
              borderBottom: "1px solid var(--color-border)",
              background: "rgba(0,0,0,0.35)",
            }}
          >
            {ticks.map((tk, i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: `${xPct(tk.ts)}%`,
                  top: 0, bottom: 0,
                  borderLeft: "1px solid var(--color-border)",
                  paddingLeft: 6,
                  paddingTop: 6,
                  fontSize: 9,
                  letterSpacing: "0.16em",
                  color: "var(--color-text-faint)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {tk.label}
              </div>
            ))}
          </div>

          {/* Lane backgrounds */}
          {LANES.map((lane, i) => {
            const top = rulerH + i * laneH;
            return (
              <div
                key={lane.key}
                style={{
                  position: "absolute",
                  left: 0, right: 0,
                  top, height: laneH,
                  borderBottom: i < LANES.length - 1 ? "1px dashed var(--color-border)" : "none",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    left: 12, top: 10,
                    fontSize: 9,
                    letterSpacing: "0.20em",
                    color: lane.color,
                    fontFamily: "var(--font-mono)",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    opacity: 0.55,
                  }}
                >
                  ── {lane.label}
                </div>
              </div>
            );
          })}

          {/* "Now" line */}
          <div
            style={{
              position: "absolute",
              left: `${xPct(now)}%`,
              top: rulerH,
              bottom: 0,
              borderLeft: "1px solid var(--color-accent)",
              boxShadow: "0 0 8px var(--color-accent-glow)",
              pointerEvents: "none",
            }}
          />

          {/* Blocks */}
          {blocks.map((b, i) => {
            const laneIdx = LANES.findIndex((l) => l.key === b.lane);
            const top = rulerH + laneIdx * laneH + 14;
            const left = xPct(b.startMs);
            const widthPct = (b.durationMs / windowMs) * 100;
            const isSelected = selected?.event === b.event;
            const color = b.isError ? "var(--color-danger)" : LANES[laneIdx].color;
            return (
              <button
                key={i}
                onClick={() => setSelected(isSelected ? null : b)}
                style={{
                  position: "absolute",
                  left: `${left}%`,
                  width: `max(${widthPct}%, 16px)`,
                  top, height: laneH - 22,
                  background: isSelected ? color : "rgba(0,0,0,0.55)",
                  border: `1px solid ${color}`,
                  boxShadow: isSelected
                    ? `0 0 14px ${color}, inset 0 0 8px ${color}`
                    : `0 0 4px ${color}`,
                  padding: "0 6px",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: isSelected ? "var(--color-bg)" : color,
                  textAlign: "left",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                }}
                title={`${b.label} · ${b.durationMs}ms · ${new Date(b.startMs).toLocaleTimeString()}`}
              >
                {b.label}
              </button>
            );
          })}

          {blocks.length === 0 && (
            <EmptyWindow
              events={events}
              windowStart={windowStart}
              windowMs={windowMs}
              onWiden={() => {
                // Jump straight to 24H, the widest window we offer.
                setWindowMs(WINDOWS[WINDOWS.length - 1].ms);
              }}
              rulerH={rulerH}
            />
          )}
        </div>

        {selected && <DetailPanel block={selected} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}

function EmptyWindow({
  events,
  windowStart,
  windowMs,
  onWiden,
  rulerH,
}: {
  events: FeedEvent[];
  windowStart: number;
  windowMs: number;
  onWiden: () => void;
  rulerH: number;
}) {
  // Find the most recent event we know about (regardless of window).
  let latest: FeedEvent | null = null;
  let latestTs = -Infinity;
  for (const e of events) {
    if (laneFor(e) === null) continue;
    const t = new Date(e.ts).getTime();
    if (t > latestTs) { latestTs = t; latest = e; }
  }

  const hasOlderEvents = latest !== null && latestTs < windowStart;
  const wholeBufferEmpty = latest === null;
  const ageMs = latest ? Date.now() - latestTs : null;
  const ageLabel = ageMs === null
    ? null
    : ageMs < 60_000
      ? `${Math.floor(ageMs / 1000)}s ago`
      : ageMs < 3_600_000
        ? `${Math.floor(ageMs / 60_000)}m ago`
        : ageMs < 86_400_000
          ? `${Math.floor(ageMs / 3_600_000)}h ago`
          : `${Math.floor(ageMs / 86_400_000)}d ago`;
  const windowLabel =
    windowMs <= 5 * 60 * 1000 ? "last 5 minutes"
    : windowMs <= 15 * 60 * 1000 ? "last 15 minutes"
    : windowMs <= 60 * 60 * 1000 ? "last hour"
    : "last 24 hours";

  return (
    <div
      style={{
        position: "absolute",
        left: 0, right: 0, top: rulerH, bottom: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        color: "var(--color-text-faint)",
        fontFamily: "var(--font-mono)",
        textAlign: "center",
        padding: 24,
      }}
    >
      <div style={{ fontSize: 11, letterSpacing: "0.20em", textTransform: "uppercase" }}>
        ── no events in {windowLabel}
      </div>

      {hasOlderEvents ? (
        <>
          <div style={{ fontSize: 12, color: "var(--color-text-dim)", letterSpacing: "0.04em" }}>
            last activity was <span style={{ color: "var(--color-accent)" }}>{ageLabel}</span>
            {latest?.event && (
              <>
                {" · "}
                <span style={{ color: "var(--color-text-muted)" }}>
                  {latest.event}
                  {latest.name ? ` · ${latest.name}` : ""}
                </span>
              </>
            )}
          </div>
          <button
            onClick={onWiden}
            style={{
              border: "1px solid var(--color-accent)",
              background: "transparent",
              color: "var(--color-accent)",
              padding: "6px 12px",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLButtonElement;
              el.style.background = "var(--color-accent)";
              el.style.color = "var(--color-bg)";
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLButtonElement;
              el.style.background = "transparent";
              el.style.color = "var(--color-accent)";
            }}
          >
            [ widen to 24h ]
          </button>
        </>
      ) : wholeBufferEmpty ? (
        <div style={{ fontSize: 12, color: "var(--color-text-muted)", letterSpacing: "0.04em" }}>
          no activity recorded yet — talk to the agent to populate
        </div>
      ) : null}
    </div>
  );
}

function buildTicks(start: number, end: number, windowMs: number) {
  // 5-6 evenly spaced ticks across the window. Scale the unit to the
  // window width so a 24H view shows hours, not 1440 minutes.
  const ticks: { ts: number; label: string }[] = [];
  const step = windowMs / 5;
  for (let i = 0; i <= 5; i++) {
    const ts = start + step * i;
    const ago = Math.max(0, end - ts);
    let label = "now";
    if (ago > 86_400_000) label = `${Math.floor(ago / 86_400_000)}D AGO`;
    else if (ago > 3_600_000) label = `${Math.floor(ago / 3_600_000)}H AGO`;
    else if (ago > 60_000) label = `${Math.floor(ago / 60_000)}M AGO`;
    else if (ago > 1000) label = `${Math.floor(ago / 1000)}S AGO`;
    else if (i !== 5) label = `${Math.floor(ago / 1000)}S`;
    ticks.push({ ts, label });
  }
  return ticks;
}

function DetailPanel({ block, onClose }: { block: Block; onClose: () => void }) {
  const e = block.event;
  const fields: [string, string][] = [
    ["EVENT",   e.event],
    ["LANE",    block.lane],
    ["SERVICE", e.service ?? "—"],
    ["TIME",    new Date(e.ts).toLocaleString()],
    ["DURATION", `${block.durationMs}ms`],
  ];
  if (e.name)   fields.push(["NAME", e.name]);
  if (e.model)  fields.push(["MODEL", e.model]);
  if (e.host)   fields.push(["HOST", e.host]);

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
        padding: 16,
        maxHeight: "calc(100vh - 200px)",
        overflowY: "auto",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 14,
        }}
      >
        <span className="brut-meta" style={{ color: "var(--color-accent)" }}>
          ── {block.label}
        </span>
        <button
          onClick={onClose}
          style={{
            border: "1px solid var(--color-border)",
            padding: "4px 8px",
            background: "transparent",
            color: "var(--color-text-muted)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            cursor: "pointer",
            letterSpacing: "0.18em",
          }}
        >
          [X CLOSE]
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "80px 1fr", rowGap: 6, columnGap: 12, marginBottom: 16 }}>
        {fields.map(([k, v]) => (
          <FieldRow key={k} k={k} v={v} />
        ))}
      </div>

      {e.text && <TextBlock label="TEXT" text={e.text} />}
      {e.args && <TextBlock label="ARGS" text={e.args} />}
      {e.result && <TextBlock label="RESULT" text={e.result} />}
    </div>
  );
}

function FieldRow({ k, v }: { k: string; v: string }) {
  return (
    <>
      <span style={{ fontSize: 9, letterSpacing: "0.20em", color: "var(--color-text-faint)" }}>{k}</span>
      <span style={{ fontSize: 11, color: "var(--color-text-dim)", overflow: "hidden", textOverflow: "ellipsis" }}>{v}</span>
    </>
  );
}

function TextBlock({ label, text }: { label: string; text: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 9, letterSpacing: "0.20em", color: "var(--color-text-faint)", marginBottom: 4 }}>
        ── {label}
      </div>
      <pre
        style={{
          margin: 0,
          padding: 10,
          background: "rgba(0,0,0,0.45)",
          border: "1px solid var(--color-border)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--color-text)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 240,
          overflow: "auto",
        }}
      >
        {text}
      </pre>
    </div>
  );
}
