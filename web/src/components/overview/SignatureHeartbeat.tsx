import { useEffect, useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";

/** SIGNATURE HEARTBEAT — the one bespoke viz that earns the product
 *  its name. Edge-to-edge ASCII waveform, full-bleed, breaks the
 *  Overview grid. Renders the last 24h of agent activity as a
 *  continuous mono character strip — one column per 5-minute bin,
 *  taller bars for more activity. Right-most column pulses live with
 *  current second's events.
 *
 *  The strip "breathes" when idle (a sine ripple drifts across)
 *  and "spikes" on the right when events arrive. The viz IS the
 *  agent's pulse — it's not decoration, it's data with one job:
 *  proving to you the thing is alive.
 */
export function SignatureHeartbeat() {
  const { events } = useEventStream(500);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);

  // 288 bins for 24h × 5min — dense enough to fill the screen,
  // coarse enough to actually show patterns.
  const bins = useMemo(() => buildBins(events, 288, now), [events, now]);
  const lastEvent = events[events.length - 1];
  const live = lastEvent && now - new Date(lastEvent.ts).getTime() < 5000;

  // Phase for the idle ripple.
  const phase = (now / 1000) % (2 * Math.PI * 4);

  return (
    <div className="relative overflow-hidden" style={{ marginBottom: 48 }}>
      <RibbonGradient />

      {/* Header strip above the waveform */}
      <div
        className="flex items-baseline justify-between mb-2 px-10"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        <span
          className="text-[10px] uppercase tracking-[0.32em]"
          style={{ color: "var(--color-text-muted)" }}
        >
          ── pulse · last 24h
        </span>
        <span
          className="text-[10px] uppercase tracking-[0.18em]"
          style={{ color: live ? "var(--color-accent)" : "var(--color-text-faint)" }}
        >
          {live ? "● spiking" : "● steady"} · {events.length} events in window
        </span>
      </div>

      {/* The waveform — bleeds full width, breaks the page grid */}
      <Strip bins={bins} phase={phase} live={!!live} />

      {/* Footer: timeline scale */}
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

function Strip({
  bins, phase, live,
}: {
  bins: number[]; // 0..1 normalized
  phase: number;
  live: boolean;
}) {
  // Render two stacked ASCII rows + a center hairline. Each row is
  // right-anchored — when the strip is wider than the container, the
  // OLD end (left) overflows; "now" stays glued to the right edge.
  const N = bins.length;
  return (
    <div
      className="relative w-full overflow-hidden"
      style={{
        background: "var(--color-bg)",
        borderTop: "1px solid var(--color-border)",
        borderBottom: "1px solid var(--color-border)",
        padding: "20px 0",
        zIndex: 1,
      }}
    >
      <Row
        text={renderRow(bins, phase, "top", live, N)}
        color="var(--color-accent)"
        glow
      />
      <Row text={centerLine(N)} color="var(--color-border-strong)" />
      <Row
        text={renderRow(bins, phase, "bottom", live, N)}
        color="var(--color-text-muted)"
      />
    </div>
  );
}

function Row({ text, color, glow }: { text: string; color: string; glow?: boolean }) {
  return (
    <div className="flex justify-end" style={{ width: "100%", overflow: "hidden" }}>
      <pre
        className="m-0 whitespace-pre"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "clamp(10px, 0.95vw, 14px)",
          lineHeight: 1.05,
          color,
          textShadow: glow ? "0 0 12px var(--color-accent-glow)" : "none",
          paddingRight: "10px",
        }}
      >
        {text}
      </pre>
    </div>
  );
}

function renderRow(bins: number[], phase: number, half: "top" | "bottom", live: boolean, N: number): string {
  return bins
    .map((v, i) => {
      // Inject a slow sine ripple when bin has no real activity,
      // so the strip never reads as "dead."
      const ripple = v === 0
        ? 0.18 + 0.18 * Math.sin(phase + i * 0.07)
        : 0;
      const effective = Math.max(v, ripple);
      const idx = Math.min(BAR_GLYPHS.length - 1, Math.round(effective * (BAR_GLYPHS.length - 1)));

      // Pulse the rightmost three bins when live.
      const isRightTip = i >= N - 3 && live;
      if (isRightTip && half === "top") {
        const boost = (Math.sin(Date.now() / 120) + 1) / 2; // 0..1
        const adj = Math.min(BAR_GLYPHS.length - 1, idx + Math.round(boost * 3));
        return BAR_GLYPHS[adj];
      }
      return BAR_GLYPHS[idx];
    })
    .join("");
}

function centerLine(N: number): string {
  return "─".repeat(N);
}

function buildBins(events: { ts: string }[], nBins: number, now: number): number[] {
  const span = 24 * 60 * 60 * 1000;
  const start = now - span;
  const counts = new Array(nBins).fill(0);
  for (const e of events) {
    const t = new Date(e.ts).getTime();
    if (t < start || t > now) continue;
    const idx = Math.floor(((t - start) / span) * nBins);
    counts[Math.min(nBins - 1, Math.max(0, idx))] += 1;
  }
  const max = Math.max(1, ...counts);
  return counts.map((c) => c / max);
}
