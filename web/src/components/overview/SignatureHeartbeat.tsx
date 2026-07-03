import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

/** SIGNATURE HEARTBEAT — the one bespoke viz that earns the product
 *  its name. Edge-to-edge mono character strip: the last 24h of agent
 *  activity, one column per 5-minute bin, taller bars for more events.
 *  The right tip pulses when events are arriving THIS second.
 *
 *  The data is real end to end: bins come from /api/stats/activity
 *  (a bounded tail read over the event log), and the live stream bumps
 *  the current bin between polls. An empty bin renders flat — the strip
 *  earns "alive" by actually being alive, never by a synthetic ripple.
 *  Hover any column for its time window and count.
 */
interface SignatureHeartbeatProps {
  /** Compact mode for a constrained cell: no header/footer, half the
   *  bin density, tighter padding. */
  compact?: boolean;
}

const HOURS = 24;

export function SignatureHeartbeat({ compact = false }: SignatureHeartbeatProps = {}) {
  const { events } = useEventStream(500);
  const [now, setNow] = useState(() => Date.now());
  const [activity, setActivity] = useState<{ since: string; bins: number[]; total: number } | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const nBins = compact ? 144 : 288;

  useEffect(() => {
    const fetchOnce = () =>
      api.statsActivity(HOURS, nBins).then(setActivity).catch(() => undefined);
    fetchOnce();
    const t = setInterval(fetchOnce, 60_000);
    return () => clearInterval(t);
  }, [nBins]);

  // Server bins + live top-up: events that streamed in since the last
  // poll land in the current (rightmost) bin so "now" never lags.
  const bins = useMemo(() => {
    const counts = activity ? [...activity.bins] : new Array(nBins).fill(0);
    if (activity) {
      const sinceMs = new Date(activity.since).getTime();
      const spanMs = HOURS * 3600 * 1000;
      const polledUpTo = sinceMs + spanMs; // ≈ poll time
      for (const e of events) {
        const t = new Date(e.ts).getTime();
        if (t > polledUpTo && t <= now) counts[counts.length - 1] += 1;
      }
    }
    return counts;
  }, [activity, events, now, nBins]);

  const maxCount = Math.max(1, ...bins);
  const total = activity?.total ?? 0;
  const lastEvent = events[events.length - 1];
  const live = lastEvent && now - new Date(lastEvent.ts).getTime() < 5000;

  return (
    <div className="relative overflow-hidden" style={{ marginBottom: compact ? 0 : 48 }}>
      <RibbonGradient />

      {!compact && (
        <div
          className="flex items-baseline justify-between mb-2 px-10"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <span
            className="text-[10px] uppercase tracking-[0.32em]"
            style={{ color: "var(--color-text-muted)" }}
          >
            ── pulse · last 24h · 5-min bins
          </span>
          <span
            className="text-[10px] uppercase tracking-[0.18em]"
            style={{ color: live ? "var(--color-accent)" : "var(--color-text-faint)" }}
          >
            {live ? "● spiking" : "● steady"} · {total} events · peak {maxCount}/bin
          </span>
        </div>
      )}

      <Strip bins={bins} maxCount={maxCount} live={!!live} compact={compact} now={now} />

      {!compact && (
        <div
          className="flex justify-between mt-2 px-10 text-[9px] uppercase tracking-[0.18em]"
          style={{ color: "var(--color-text-faint)", fontFamily: "var(--font-mono)" }}
        >
          <span>−24h</span>
          <span>−18h</span>
          <span>−12h</span>
          <span>−6h</span>
          <span>now ▸</span>
        </div>
      )}
    </div>
  );
}

function RibbonGradient() {
  // A radial wash behind the strip — phosphor warmth on the right
  // edge to anchor "now," fade off to the left.
  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{
        background:
          "radial-gradient(ellipse at 100% 50%, rgba(124,254,0,0.10), transparent 60%)",
        zIndex: 0,
      }}
    />
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

function Strip({
  bins, maxCount, live, compact = false, now,
}: {
  bins: number[];
  maxCount: number;
  live: boolean;
  compact?: boolean;
  now: number;
}) {
  const N = bins.length;
  const binMs = (HOURS * 3600 * 1000) / N;

  // Top row: one span per bin so each column can carry a native
  // tooltip (time window + count). Bottom row mirrors as a single
  // string — it's the reflection, not a second hover target.
  const boost = live ? Math.round(((Math.sin(now / 120) + 1) / 2) * 3) : 0;
  const topSpans = bins.map((c, i) => {
    const isTip = i >= N - 3 && live;
    const g = glyphFor(c, maxCount, isTip ? boost : 0);
    const from = new Date(now - (N - i) * binMs);
    const hh = String(from.getHours()).padStart(2, "0");
    const mm = String(from.getMinutes()).padStart(2, "0");
    return (
      <span key={i} title={`${hh}:${mm} · ${c} event${c === 1 ? "" : "s"}`}>
        {g}
      </span>
    );
  });
  const bottomText = bins
    .map((c, i) => glyphFor(c, maxCount, i >= N - 3 && live ? boost : 0))
    .join("");

  return (
    <div
      className="relative w-full overflow-hidden"
      style={{
        background: "var(--color-bg)",
        borderTop: "1px solid var(--color-border)",
        borderBottom: "1px solid var(--color-border)",
        padding: compact ? "10px 0" : "20px 0",
        zIndex: 1,
      }}
    >
      <div className="flex justify-end" style={{ width: "100%", overflow: "hidden" }}>
        <pre
          className="m-0 whitespace-pre"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "clamp(10px, 0.95vw, 14px)",
            lineHeight: 1.05,
            color: "var(--color-accent)",
            textShadow: "0 0 12px var(--color-accent-glow)",
            paddingRight: "10px",
          }}
        >
          {topSpans}
        </pre>
      </div>
      <Row text={"─".repeat(N)} color="var(--color-border-strong)" />
      <Row text={bottomText} color="var(--color-text-muted)" ariaHidden />
    </div>
  );
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
