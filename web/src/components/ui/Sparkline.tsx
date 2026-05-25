/** ASCII sparkline — bins ISO timestamps into N slots over a window
 *  and renders them as block characters. Mono-friendly, brutalist.
 *
 *  Empty bins render as `▁` (lowest visible bar), not a space, so the
 *  sparkline always reads as a continuous waveform with spikes above
 *  the baseline. A single call surrounded by zeros is visually a spike,
 *  not an isolated block. */
const GLYPHS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];

interface Props {
  timestamps: string[]; // ISO datetimes
  bins?: number;
  windowMs?: number; // how far back to look; default 24h
  color?: string;
  glow?: boolean;
  className?: string;
}

export function Sparkline({
  timestamps, bins = 24, windowMs = 24 * 60 * 60 * 1000, color, glow, className,
}: Props) {
  const now = Date.now();
  const start = now - windowMs;
  const counts = new Array(bins).fill(0) as number[];
  for (const ts of timestamps) {
    const t = new Date(ts).getTime();
    if (isNaN(t) || t < start || t > now) continue;
    const idx = Math.min(bins - 1, Math.floor(((t - start) / windowMs) * bins));
    counts[idx] += 1;
  }
  // Min max=3 so a single call doesn't max out the bar — visually scales
  // call volume rather than flagging every solo call as "full intensity."
  const max = Math.max(3, ...counts);
  const str = counts
    .map((c) => {
      if (c === 0) return GLYPHS[0]; // baseline ▁ so empty stretches stay visible
      // Map non-zero counts across the upper 7 glyphs (skip the baseline).
      const intensity = Math.min(1, c / max);
      const idx = 1 + Math.round(intensity * (GLYPHS.length - 2));
      return GLYPHS[idx];
    })
    .join("");

  return (
    <span
      className={className}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: color ?? "var(--color-accent)",
        textShadow: glow ? "0 0 8px var(--color-accent-glow)" : "none",
        letterSpacing: 0,
        whiteSpace: "pre",
      }}
    >
      {str}
    </span>
  );
}
