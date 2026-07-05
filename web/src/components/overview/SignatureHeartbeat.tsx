import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

/** SIGNATURE HEARTBEAT — the pulse strip in the Overview status grid.
 *  The last 24h of agent activity rendered btop-style in braille
 *  characters: each char cell carries two 5-minute bins (left/right dot
 *  columns) across two rows (8 vertical dot levels), so the curve is
 *  twice as sharp as the old block glyphs at the same strip width. The
 *  right tip pulses when events are arriving THIS second, and a live
 *  ev/min readout sits at the "now" edge (the btop convention: current
 *  value printed where the graph meets the present).
 *
 *  The data is real end to end: bins come from /api/stats/activity
 *  (a bounded tail read over the event log), and the live stream bumps
 *  the current bin between polls. An empty bin renders flat — the strip
 *  earns "alive" by actually being alive, never by a synthetic ripple.
 */
const HOURS = 24;
const N_BINS = 288; // 5-min bins; two per braille char → 144 chars wide
const N_CHARS = N_BINS / 2;

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
  const charMs = binMs * 2;

  // Live rate at the "now" tip — events observed in the last 60s.
  const perMin = events.filter((e) => now - new Date(e.ts).getTime() < 60_000).length;

  // Char windows anchor to the SERVER window start, not the ticking
  // clock, so a label never changes under the cursor.
  const label = (i: number) => {
    const from = sinceMs + i * charMs;
    const c = (bins[2 * i] ?? 0) + (bins[2 * i + 1] ?? 0);
    return `${fmtHM(from)}–${fmtHM(from + charMs)} · ${c} event${c === 1 ? "" : "s"}`;
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

  // Per-bin dot level 0..8. A non-zero bin always shows at least one
  // dot — one event is still an event.
  const boost = live ? Math.round(((Math.sin(now / 120) + 1) / 2) * 3) : 0;
  const level = (bin: number, count: number): number => {
    const boosted = bin >= N_BINS - 4 && live ? boost : 0;
    if (count <= 0 && boosted <= 0) return 0;
    const v = Math.min(1, count / maxCount);
    return Math.max(count > 0 ? 1 : 0, Math.min(8, Math.round(v * 8) + boosted));
  };

  const { topChars, bottomChars } = useMemo(() => {
    const top: string[] = [];
    const bottom: string[] = [];
    for (let i = 0; i < N_CHARS; i++) {
      const l = level(2 * i, bins[2 * i] ?? 0);
      const r = level(2 * i + 1, bins[2 * i + 1] ?? 0);
      top.push(brailleCell(Math.max(l - 4, 0), Math.max(r - 4, 0)));
      bottom.push(brailleCell(Math.min(l, 4), Math.min(r, 4)));
    }
    return { topChars: top, bottomChars: bottom };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bins, maxCount, live, boost]);

  return (
    <div className="relative overflow-hidden">
      {/* Phosphor warmth anchoring "now" at the right edge. */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse at 100% 50%, color-mix(in srgb, var(--color-accent) 10%, transparent), transparent 60%)",
          zIndex: 0,
        }}
      />
      <div
        className="hm-screen-well relative w-full overflow-hidden"
        style={{
          // Screen well: the strip is a CRT readout, so it gets the true
          // black the lifted page field gave up.
          background: "var(--color-screen)",
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
          onMouseOver={handleOver}
          onMouseLeave={() => setHover(null)}
          style={{ cursor: "crosshair" }}
        >
          <GraphRow chars={topChars} glow />
          <GraphRow chars={bottomChars} glow />
        </div>
        <Row text={"─".repeat(N_CHARS)} color="var(--color-border-strong)" />
        {/* Phosphor afterglow — the lower row's dim persistence. */}
        <Row text={bottomChars.join("")} color="var(--color-text-muted)" ariaHidden />
        {/* Live rate at the now-tip, the btop convention. */}
        <div
          className="absolute text-[9px] uppercase tracking-[0.16em]"
          style={{
            right: 10,
            bottom: 6,
            fontFamily: "var(--font-mono)",
            fontVariantNumeric: "tabular-nums",
            color: live ? "var(--color-accent-ink)" : "var(--color-text-faint)",
            zIndex: 2,
            pointerEvents: "none",
          }}
        >
          ▸ {perMin}/min
        </div>
      </div>
    </div>
  );

  function GraphRow({ chars, glow }: { chars: string[]; glow?: boolean }) {
    return (
      <div className="flex justify-end" style={{ width: "100%", overflow: "hidden" }}>
        <pre
          className="m-0 whitespace-pre"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "clamp(10px, 0.95vw, 14px)",
            lineHeight: 1.0,
            color: "var(--color-accent)",
            textShadow: glow ? "0 0 12px var(--color-accent-glow)" : "none",
            paddingRight: "10px",
          }}
        >
          {chars.map((ch, i) => (
            <span key={i} data-i={i} aria-label={label(i)}>
              {ch}
            </span>
          ))}
        </pre>
      </div>
    );
  }
}

// Braille dot columns, filled bottom-up. Left column: dots 7,3,2,1;
// right column: dots 8,6,5,4 (Unicode braille bit layout).
const BRAILLE_LEFT = [0x00, 0x40, 0x44, 0x46, 0x47];
const BRAILLE_RIGHT = [0x00, 0x80, 0xa0, 0xb0, 0xb8];

function brailleCell(left: number, right: number): string {
  return String.fromCharCode(0x2800 + BRAILLE_LEFT[left] + BRAILLE_RIGHT[right]);
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
