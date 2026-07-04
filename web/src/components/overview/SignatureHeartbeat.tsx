import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

/** SIGNATURE HEARTBEAT — the pulse strip in the Overview status grid.
 *  The last 24h of agent activity as a mono character strip: one column
 *  per 10-minute bin, taller bars for more events. The right tip pulses
 *  when events are arriving THIS second.
 *
 *  The data is real end to end: bins come from /api/stats/activity
 *  (a bounded tail read over the event log), and the live stream bumps
 *  the current bin between polls. An empty bin renders flat — the strip
 *  earns "alive" by actually being alive, never by a synthetic ripple.
 *  Hovering a column shows an .hm-tooltip readout tracking the column
 *  (aria-labels carry the same text for assistive tech — no native
 *  title, which would double-render the OS bubble).
 */
const HOURS = 24;
const N_BINS = 144;

export function SignatureHeartbeat() {
  const { events } = useEventStream(500);
  const [now, setNow] = useState(() => Date.now());
  const [activity, setActivity] = useState<{ since: string; bins: number[]; total: number } | null>(null);
  const [hover, setHover] = useState<{ label: string; x: number } | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const fetchOnce = () =>
      api.statsActivity(HOURS, N_BINS).then(setActivity).catch(() => undefined);
    fetchOnce();
    const t = setInterval(fetchOnce, 60_000);
    return () => clearInterval(t);
  }, []);

  // Server bins + live top-up: events that streamed in since the last
  // poll land in the current (rightmost) bin so "now" never lags.
  const { bins, sinceMs } = useMemo(() => {
    const counts = activity ? [...activity.bins] : new Array(N_BINS).fill(0);
    const since = activity ? new Date(activity.since).getTime() : now - HOURS * 3600 * 1000;
    if (activity) {
      const polledUpTo = since + HOURS * 3600 * 1000; // ≈ poll time
      for (const e of events) {
        const t = new Date(e.ts).getTime();
        if (t > polledUpTo && t <= now) counts[counts.length - 1] += 1;
      }
    }
    return { bins: counts, sinceMs: since };
  }, [activity, events, now]);

  const maxCount = Math.max(1, ...bins);
  const lastEvent = events[events.length - 1];
  const live = lastEvent && now - new Date(lastEvent.ts).getTime() < 5000;
  const binMs = (HOURS * 3600 * 1000) / N_BINS;

  // Bin windows anchor to the SERVER window start, not the ticking
  // clock, so a label never changes under the cursor.
  const label = (i: number) => {
    const from = sinceMs + i * binMs;
    const c = bins[i];
    return `${fmtHM(from)}–${fmtHM(from + binMs)} · ${c} event${c === 1 ? "" : "s"}`;
  };

  // One delegated handler instead of 144 listeners. The strip is
  // right-anchored, so the column's x is measured, not index-derived.
  const handleOver = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    const idx = t.dataset?.i;
    if (idx === undefined) {
      setHover(null);
      return;
    }
    const wrap = e.currentTarget as HTMLElement;
    const r = t.getBoundingClientRect();
    setHover({
      label: label(Number(idx)),
      x: r.left + r.width / 2 - wrap.getBoundingClientRect().left,
    });
  };

  const boost = live ? Math.round(((Math.sin(now / 120) + 1) / 2) * 3) : 0;
  const topSpans = bins.map((c: number, i: number) => (
    <span key={i} data-i={i} aria-label={label(i)}>
      {glyphFor(c, maxCount, i >= N_BINS - 3 && live ? boost : 0)}
    </span>
  ));
  const bottomText = bins
    .map((c: number, i: number) => glyphFor(c, maxCount, i >= N_BINS - 3 && live ? boost : 0))
    .join("");

  return (
    <div className="relative overflow-hidden">
      {/* Phosphor warmth anchoring "now" at the right edge. */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse at 100% 50%, rgba(124,254,0,0.10), transparent 60%)",
          zIndex: 0,
        }}
      />
      <div
        className="relative w-full overflow-hidden"
        style={{
          background: "var(--color-bg)",
          borderTop: "1px solid var(--color-border)",
          borderBottom: "1px solid var(--color-border)",
          padding: "10px 0",
          zIndex: 1,
        }}
      >
        {hover && (
          // The .hm-tooltip shell (same design as every other hint in the
          // app), tracking the hovered column; clamped so it stays inside
          // the strip's overflow-hidden bounds at the edges.
          <div
            className="hm-tooltip"
            style={{
              position: "absolute",
              top: 2,
              left: `clamp(70px, ${hover.x}px, calc(100% - 70px))`,
              transform: "translateX(-50%)",
              whiteSpace: "nowrap",
              fontVariantNumeric: "tabular-nums",
              zIndex: 2,
              pointerEvents: "none",
            }}
          >
            {hover.label}
          </div>
        )}
        <div
          className="flex justify-end"
          style={{ width: "100%", overflow: "hidden" }}
          onMouseOver={handleOver}
          onMouseLeave={() => setHover(null)}
        >
          <pre
            className="m-0 whitespace-pre"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "clamp(10px, 0.95vw, 14px)",
              lineHeight: 1.05,
              color: "var(--color-accent)",
              textShadow: "0 0 12px var(--color-accent-glow)",
              paddingRight: "10px",
              cursor: "crosshair",
            }}
          >
            {topSpans}
          </pre>
        </div>
        <Row text={"─".repeat(N_BINS)} color="var(--color-border-strong)" />
        <Row text={bottomText} color="var(--color-text-muted)" ariaHidden />
      </div>
    </div>
  );
}

const BAR_GLYPHS = [" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];

function glyphFor(count: number, maxCount: number, boost = 0): string {
  if (count <= 0 && boost <= 0) return BAR_GLYPHS[0];
  const v = Math.min(1, count / maxCount);
  // Non-zero bins always show at least ▁ — one event is still an event.
  const idx = Math.max(count > 0 ? 1 : 0,
    Math.min(BAR_GLYPHS.length - 1, Math.round(v * (BAR_GLYPHS.length - 1)) + boost));
  return BAR_GLYPHS[Math.min(BAR_GLYPHS.length - 1, idx)];
}

function fmtHM(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function Row({ text, color, ariaHidden }: { text: string; color: string; ariaHidden?: boolean }) {
  return (
    <div
      className="flex justify-end"
      style={{ width: "100%", overflow: "hidden" }}
      aria-hidden={ariaHidden}
    >
      <pre
        className="m-0 whitespace-pre"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "clamp(10px, 0.95vw, 14px)",
          lineHeight: 1.05,
          color,
          paddingRight: "10px",
        }}
      >
        {text}
      </pre>
    </div>
  );
}
