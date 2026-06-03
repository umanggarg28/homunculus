import { useEffect, useRef, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { FeedRow } from "./FeedRow";

// Infrastructure-only events. Hidden by default; viewable via the SYS chip.
const SYSTEM_EVENTS = new Set([
  "service_ping",
  "provider_cooled",
  "context_compacted",
  "budget_blocked",
  "agent_controls_updated",
]);

/**
 * Filter chips behave inclusively:
 *   - No chip selected → show all real (non-system) events. Default.
 *   - Click any chip(s) → show only events matching the selected categories.
 *   - SYS chip → show only the system/infra events (service_pings etc.).
 *     SYS is the only way to surface system events; it doesn't "add" to
 *     other chips — selecting SYS alone shows pings, selecting SYS + USER
 *     shows pings + user messages, etc.
 */
const EVENT_TYPE_FILTERS = [
  { label: "USER",  match: (t: string) => t === "user_message" },
  { label: "LLM",   match: (t: string) => t === "llm_call" },
  { label: "TOOL",  match: (t: string) => t === "tool_call" || t === "tool_result" },
  { label: "REPLY", match: (t: string) => t === "assistant_reply" },
  { label: "SYS",   match: (t: string) => SYSTEM_EVENTS.has(t) },
] as const;

type TypeLabel = (typeof EVENT_TYPE_FILTERS)[number]["label"];

/** Brutalist trace stream — hairline-bordered container, no card. */
export function FeedStream() {
  const { events } = useEventStream(300);
  const [query, setQuery] = useState("");
  const [activeTypes, setActiveTypes] = useState<Set<TypeLabel>>(new Set());
  const endRef = useRef<HTMLDivElement>(null);

  // Counts for display chips. Real = non-system, sys = system events.
  const realCount = events.filter((e) => !SYSTEM_EVENTS.has(e.event)).length;
  const sysCount = events.length - realCount;

  // Filter by type: empty set = show all real events (system hidden);
  // any selection = show only events matching at least one chip.
  const byType = activeTypes.size === 0
    ? events.filter((e) => !SYSTEM_EVENTS.has(e.event))
    : events.filter((e) =>
        EVENT_TYPE_FILTERS.some((f) => activeTypes.has(f.label) && f.match(e.event))
      );

  // Search filter on top of type filter.
  const q = query.trim().toLowerCase();
  const visible = q
    ? byType.filter((e) => {
        const haystack = [e.event, e.service, e.text, e.args, e.result, e.name, e.model]
          .filter(Boolean).join(" ").toLowerCase();
        return haystack.includes(q);
      })
    : byType;

  useEffect(() => {
    if (query || activeTypes.size > 0) return;
    const nearBottom =
      window.innerHeight + window.scrollY > document.body.offsetHeight - 200;
    if (nearBottom) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length, query, activeTypes.size]);

  const toggleType = (label: TypeLabel) => {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label); else next.add(label);
      return next;
    });
  };

  if (events.length === 0) {
    return (
      <div
        className="instrument-panel hm-panel-scan hm-panel-secondary p-8 text-center text-[11px] uppercase tracking-[0.16em]"
        style={{
          color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        ─ waiting for first event ─
      </div>
    );
  }

  return (
    <div>
      {/* Filter bar */}
      <div
        style={{
          display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap",
          fontFamily: "var(--font-mono)", fontSize: 10, alignItems: "center",
        }}
      >
        {EVENT_TYPE_FILTERS.map((f) => {
          const on = activeTypes.has(f.label);
          // Show per-chip count for SYS so users know how many pings exist.
          const labelText = f.label === "SYS" && sysCount > 0 && !on
            ? `SYS (${sysCount})`
            : f.label;
          return (
            <button key={f.label} onClick={() => toggleType(f.label)} style={{
              padding: "2px 8px",
              border: `1px solid ${on ? "var(--color-accent)" : "var(--color-border)"}`,
              background: on ? "var(--color-accent)" : "transparent",
              color: on ? "var(--color-bg)" : "var(--color-text-faint)",
              cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 9,
              letterSpacing: "0.14em", textTransform: "uppercase",
            }}>{labelText}</button>
          );
        })}

        {/* Search box */}
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search events…"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--color-border)",
            color: "var(--color-text)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            padding: "2px 8px",
            outline: "none",
            width: 160,
            letterSpacing: "0.06em",
          }}
        />

        {/* Result count when filtering */}
        {(q || activeTypes.size > 0) && (
          <span style={{ color: "var(--color-text-faint)", fontSize: 9, letterSpacing: "0.10em" }}>
            {visible.length}/{activeTypes.has("SYS") ? events.length : realCount}
          </span>
        )}

        {/* Clear */}
        {(q || activeTypes.size > 0) && (
          <button onClick={() => { setQuery(""); setActiveTypes(new Set()); }} style={{
            background: "none", border: "1px solid var(--color-border)",
            color: "var(--color-text-faint)", padding: "2px 7px",
            cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 9,
            letterSpacing: "0.10em",
          }}>✕ clear</button>
        )}
      </div>

      <div className="instrument-panel hm-panel-scan hm-panel-secondary" style={{ fontFamily: "var(--font-mono)" }}>
        {visible.length === 0 ? (
          <div
            className="p-8 text-center text-[11px] uppercase tracking-[0.16em]"
            style={{ color: "var(--color-text-muted)" }}
          >
            {q || activeTypes.size > 0 ? "─ no matching events ─" : "─ no agent activity yet ─"}
          </div>
        ) : (
          visible.map((e, i) => <FeedRow key={`${e.ts}-${i}`} event={e} />)
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
