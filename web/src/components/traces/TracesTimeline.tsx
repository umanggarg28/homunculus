import { useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { FeedEvent } from "@/lib/types";

/**
 * Turn-based waterfall trace view (LangSmith / Jaeger pattern).
 *
 * Each user_message starts a new "turn" row. Within the row, every
 * subsequent event (LLM call, tool call, guard, reply) is laid out on
 * a shared horizontal time axis as a colored pill. The causal chain is
 * immediately visible: message → LLM → tool → LLM → reply.
 *
 * Replaces the swimlane Gantt which left mostly-empty lanes and broke
 * the causal flow across disconnected rows.
 */

interface TurnEvent {
  event: FeedEvent;
  startMs: number;
  durationMs: number;
}

interface Turn {
  id: string;
  userEvent: FeedEvent;
  startMs: number;
  endMs: number;
  events: TurnEvent[];
  service: string;
}

const EVENT_COLOR: Record<string, string> = {
  user_message:    "var(--color-indigo)",
  llm_call:        "var(--color-accent)",
  tool_call:       "var(--color-warning)",
  tool_result:     "var(--color-warning)",
  assistant_reply: "var(--color-accent)",
  output_guard:    "var(--color-danger)",
  self_correction: "var(--color-amber)",
  context_compacted: "var(--color-text-faint)",
};

const EVENT_LABEL: Record<string, string> = {
  user_message:    "MSG",
  llm_call:        "LLM",
  tool_call:       "TOOL",
  tool_result:     "RES",
  assistant_reply: "REPLY",
  output_guard:    "GUARD",
  self_correction: "FIX",
  context_compacted: "COMPACT",
};

function colorFor(e: FeedEvent): string {
  if (e.event === "tool_result" && typeof e.result === "string" && /^error|fail/i.test(e.result)) {
    return "var(--color-danger)";
  }
  return EVENT_COLOR[e.event] ?? "var(--color-text-faint)";
}

function buildTurns(events: FeedEvent[]): Turn[] {
  const sorted = [...events].sort(
    (a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime(),
  );

  const turns: Turn[] = [];
  let current: Turn | null = null;

  for (let i = 0; i < sorted.length; i++) {
    const e = sorted[i];
    const t = new Date(e.ts).getTime();

    if (e.event === "user_message") {
      // Close previous turn
      if (current) turns.push(current);
      current = {
        id: `${e.ts}-${i}`,
        userEvent: e,
        startMs: t,
        endMs: t,
        events: [{ event: e, startMs: t, durationMs: 0 }],
        service: e.service ?? "?",
      };
    } else if (current) {
      current.events.push({ event: e, startMs: t, durationMs: 0 });
      current.endMs = t;
    }
  }
  if (current) turns.push(current);

  // Back-fill durations: each event's duration = next event's start - this start, capped at 8s
  for (const turn of turns) {
    const evts = turn.events;
    for (let i = 0; i < evts.length; i++) {
      const next = evts[i + 1];
      const raw = next ? next.startMs - evts[i].startMs : 400;
      evts[i].durationMs = Math.min(8000, Math.max(80, raw));
    }
    // Last event in turn: duration = turn end - last start, minimum 400ms
    if (evts.length > 0) {
      const last = evts[evts.length - 1];
      last.durationMs = Math.max(400, turn.endMs - last.startMs + 400);
    }
  }

  return turns.reverse(); // newest first
}

function formatAge(ms: number): string {
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}:${d.getSeconds().toString().padStart(2, "0")}`;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function TracesTimeline() {
  const { events, connected } = useEventStream(500);
  const [selected, setSelected] = useState<TurnEvent | null>(null);
  const [expandedTurn, setExpandedTurn] = useState<string | null>(null);

  const turns = useMemo(() => buildTurns(events), [events]);

  const totalTurnMs = (t: Turn) => Math.max(1, t.endMs - t.startMs + 400);

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
          fontFamily: "var(--font-mono)",
        }}
      >
        <span className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
          ── {turns.length} turn{turns.length !== 1 ? "s" : ""} · newest first
        </span>
        <span
          style={{
            color: connected ? "var(--color-accent)" : "var(--color-warning)",
            fontSize: 10,
            letterSpacing: "0.16em",
            textShadow: connected ? "0 0 6px var(--color-accent-glow)" : "none",
          }}
        >
          ● {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>

      {turns.length === 0 ? (
        <div
          style={{
            padding: "32px 0",
            textAlign: "center",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            letterSpacing: "0.18em",
            color: "var(--color-text-faint)",
            border: "1px solid var(--color-border)",
          }}
        >
          ── no turns yet — talk to the agent to populate ──
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {turns.map((turn) => {
            const spanMs = totalTurnMs(turn);
            const isExpanded = expandedTurn === turn.id;
            const turnDuration = turn.endMs - turn.startMs;
            const hasError = turn.events.some(
              (te) => te.event.event === "output_guard" ||
                (te.event.event === "tool_result" && typeof te.event.result === "string" && /^error|fail/i.test(te.event.result))
            );

            return (
              <div
                key={turn.id}
                style={{
                  border: `1px solid ${hasError ? "var(--color-danger)" : "var(--color-border)"}`,
                  borderLeft: `3px solid ${hasError ? "var(--color-danger)" : "var(--color-border-strong)"}`,
                  background: hasError ? "rgba(255,77,46,0.03)" : "var(--color-surface-1)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {/* Turn header row */}
                <div
                  onClick={() => setExpandedTurn(isExpanded ? null : turn.id)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "60px 80px 1fr auto",
                    gap: "0 16px",
                    alignItems: "center",
                    padding: "8px 12px",
                    cursor: "pointer",
                    borderBottom: isExpanded ? "1px solid var(--color-border)" : "none",
                  }}
                >
                  {/* Time */}
                  <span style={{ fontSize: 10, color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums" }}>
                    {formatTime(turn.startMs)}
                  </span>

                  {/* Service badge */}
                  <span style={{ fontSize: 9, letterSpacing: "0.18em", color: "var(--color-text-muted)", textTransform: "uppercase" }}>
                    {turn.service}
                  </span>

                  {/* Mini waterfall — the entire turn compressed to one line */}
                  <div style={{ position: "relative", height: 16, overflow: "hidden" }}>
                    {turn.events.map((te, i) => {
                      const leftPct = ((te.startMs - turn.startMs) / spanMs) * 100;
                      const widthPct = Math.max(0.5, (te.durationMs / spanMs) * 100);
                      const color = colorFor(te.event);
                      const isSelected_ = selected?.event === te.event;
                      return (
                        <div
                          key={i}
                          onClick={(ev) => { ev.stopPropagation(); setSelected(isSelected_ ? null : te); setExpandedTurn(turn.id); }}
                          title={`${EVENT_LABEL[te.event.event] ?? te.event.event}${te.event.name ? ` · ${te.event.name}` : ""} · ${formatDuration(te.durationMs)}`}
                          style={{
                            position: "absolute",
                            left: `${leftPct}%`,
                            width: `max(${widthPct}%, 4px)`,
                            top: 3,
                            height: 10,
                            borderRadius: 2,
                            background: isSelected_
                              ? color
                              : `color-mix(in srgb, ${color} 35%, transparent)`,
                            border: `1px solid ${color}`,
                            boxShadow: isSelected_ ? `0 0 6px ${color}` : "none",
                            cursor: "pointer",
                          }}
                        />
                      );
                    })}
                  </div>

                  {/* Duration + expand indicator */}
                  <div style={{ display: "flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
                    {turnDuration > 0 && (
                      <span style={{ fontSize: 9, color: "var(--color-text-faint)", letterSpacing: "0.08em" }}>
                        {formatDuration(turnDuration)}
                      </span>
                    )}
                    <span style={{ fontSize: 9, color: "var(--color-text-faint)", letterSpacing: "0.08em" }}>
                      {formatAge(turn.startMs)}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--color-text-muted)" }}>
                      {isExpanded ? "▲" : "▼"}
                    </span>
                  </div>
                </div>

                {/* User message preview */}
                <div
                  onClick={() => setExpandedTurn(isExpanded ? null : turn.id)}
                  style={{
                    padding: "4px 12px 6px",
                    fontSize: 11,
                    color: "var(--color-text-dim)",
                    borderBottom: isExpanded ? "1px solid var(--color-border)" : "none",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ color: "var(--color-indigo)", marginRight: 8 }}>$</span>
                  {turn.userEvent.text ?? "(no text)"}
                </div>

                {/* Expanded detail — event list + selected panel */}
                {isExpanded && (
                  <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 300px" : "1fr" }}>
                    {/* Event list */}
                    <div>
                      {turn.events.map((te, i) => {
                        const color = colorFor(te.event);
                        const isSelected_ = selected?.event === te.event;
                        const label = te.event.name
                          ? `${te.event.event.replace(/_/g, " ")} · ${te.event.name}`
                          : te.event.event.replace(/_/g, " ");
                        const preview =
                          te.event.text?.slice(0, 120) ??
                          te.event.args?.slice(0, 120) ??
                          te.event.result?.slice(0, 120) ??
                          (te.event.model ? `${te.event.model} via ${te.event.host ?? ""}` : "");

                        return (
                          <div
                            key={i}
                            onClick={() => setSelected(isSelected_ ? null : te)}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "18px 110px 1fr 50px",
                              gap: "0 10px",
                              alignItems: "start",
                              padding: "5px 12px",
                              borderBottom: "1px solid var(--color-border)",
                              cursor: "pointer",
                              background: isSelected_ ? "rgba(255,255,255,0.03)" : "transparent",
                              fontSize: 11,
                            }}
                          >
                            <span style={{ color, paddingTop: 1 }}>
                              {te.event.event === "user_message" ? "$"
                                : te.event.event === "llm_call" ? "λ"
                                : te.event.event === "tool_call" ? "→"
                                : te.event.event === "tool_result" ? "←"
                                : te.event.event === "assistant_reply" ? "›"
                                : te.event.event === "output_guard" ? "⚠"
                                : te.event.event === "self_correction" ? "↺"
                                : "·"}
                            </span>
                            <span style={{ color, fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase", paddingTop: 2 }}>
                              {label}
                            </span>
                            <span style={{ color: "var(--color-text-faint)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              {preview}
                            </span>
                            <span style={{ color: "var(--color-text-faint)", fontSize: 9, textAlign: "right" }}>
                              {formatDuration(te.durationMs)}
                            </span>
                          </div>
                        );
                      })}
                    </div>

                    {/* Detail panel */}
                    {selected && (() => {
                      const e = selected.event;
                      return (
                        <div
                          style={{
                            borderLeft: "1px solid var(--color-border)",
                            padding: 14,
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            overflowY: "auto",
                            maxHeight: 340,
                            background: "rgba(0,0,0,0.25)",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                            <span style={{ fontSize: 9, letterSpacing: "0.18em", color: "var(--color-accent)" }}>
                              ── {(EVENT_LABEL[e.event] ?? e.event).toUpperCase()}
                            </span>
                            <button
                              onClick={() => setSelected(null)}
                              style={{ border: "none", background: "transparent", color: "var(--color-text-faint)", cursor: "pointer", fontSize: 11 }}
                            >
                              ✕
                            </button>
                          </div>
                          <KV k="time" v={formatTime(selected.startMs)} />
                          <KV k="service" v={e.service ?? "—"} />
                          <KV k="duration" v={formatDuration(selected.durationMs)} />
                          {e.model && <KV k="model" v={e.model} />}
                          {e.host && <KV k="host" v={e.host} />}
                          {e.name && <KV k="name" v={e.name} />}
                          {e.text && <Pre label="text" text={e.text} />}
                          {e.args && <Pre label="args" text={e.args} />}
                          {e.result && <Pre label="result" text={e.result} />}
                        </div>
                      );
                    })()}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "60px 1fr", gap: "0 8px", marginBottom: 4 }}>
      <span style={{ fontSize: 9, letterSpacing: "0.16em", color: "var(--color-text-faint)", textTransform: "uppercase" }}>{k}</span>
      <span style={{ fontSize: 10, color: "var(--color-text-dim)", overflow: "hidden", textOverflow: "ellipsis" }}>{v}</span>
    </div>
  );
}

function Pre({ label, text }: { label: string; text: string }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 9, letterSpacing: "0.16em", color: "var(--color-text-faint)", marginBottom: 4, textTransform: "uppercase" }}>
        ── {label}
      </div>
      <pre style={{
        margin: 0, padding: 8,
        background: "rgba(0,0,0,0.4)",
        border: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--color-text)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        maxHeight: 200,
        overflow: "auto",
      }}>
        {text}
      </pre>
    </div>
  );
}
