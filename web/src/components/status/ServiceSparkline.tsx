import { useMemo } from "react";
import { useEventStream } from "@/hooks/useEventStream";

interface Props {
  service: string;
  state: string;
  /** Lookback window for the sparkline, seconds. Default 5 min. */
  windowSeconds?: number;
  width?: number;
  height?: number;
}

/** A tiny per-service activity waveform.
 *
 * Each pixel-column counts events from that service in a small time
 * bucket; tallest bar = busiest moment in the lookback window.
 * Replaces the static pulsing dot — you actually see when a service
 * has been doing things. */
export function ServiceSparkline({
  service,
  state,
  windowSeconds = 300,
  width = 56,
  height = 14,
}: Props) {
  const { events } = useEventStream(500);
  const buckets = useBuckets(events, service, windowSeconds, width);

  const max = Math.max(...buckets, 1);
  const color =
    state === "live" ? "var(--color-signal)"
    : state === "idle" ? "var(--color-warning)"
    : state === "stale" ? "var(--color-danger)"
    : "var(--color-text-faint)";

  return (
    <svg width={width} height={height} aria-hidden style={{ display: "block" }}>
      {buckets.map((v, i) => {
        const h = Math.max(1, (v / max) * (height - 2));
        return (
          <rect
            key={i}
            x={i}
            y={height - h}
            width={1}
            height={h}
            fill={color}
            opacity={v === 0 ? 0.18 : 0.85}
          />
        );
      })}
    </svg>
  );
}

function useBuckets(
  events: Array<{ ts: string; service: string }>,
  service: string,
  windowSeconds: number,
  buckets: number,
): number[] {
  return useMemo(() => {
    const now = Date.now();
    const bucketMs = (windowSeconds * 1000) / buckets;
    const result = new Array<number>(buckets).fill(0);
    for (const e of events) {
      if (e.service !== service) continue;
      const t = new Date(e.ts).getTime();
      const ageMs = now - t;
      if (ageMs < 0 || ageMs > windowSeconds * 1000) continue;
      const idx = buckets - 1 - Math.floor(ageMs / bucketMs);
      if (idx >= 0 && idx < buckets) result[idx] += 1;
    }
    return result;
  }, [events, service, windowSeconds, buckets]);
}
