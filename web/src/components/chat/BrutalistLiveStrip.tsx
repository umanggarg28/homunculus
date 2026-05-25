import { useEffect, useRef, useState } from "react";

interface Props {
  sending: boolean;
  lastToolName?: string;
  toolCount: number;
}

/** Live activity strip — sits above the chat input, never empty.
 *
 *   MCP/16 ── 03 CALLS ── LAST: read_file ── ▁▁▂▁▁▇▁▁▅▁▁▂▁▂▁▁▇▁
 *
 *  The heartbeat strip is a 32-bar ASCII waveform that drifts when
 *  the agent is idle and spikes when it's working. Animation runs
 *  client-side off requestAnimationFrame — no network.
 */
export function BrutalistLiveStrip({ sending, lastToolName, toolCount }: Props) {
  const bars = useHeartbeatBars(sending);

  return (
    <div
      className="flex items-center gap-5 text-[10px] uppercase tracking-[0.16em] mb-2 px-1"
      style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
    >
      <span style={{ color: "var(--color-accent)" }}>MCP/16</span>
      <Sep />
      <span>
        {toolCount.toString().padStart(2, "0")} CALL{toolCount === 1 ? "" : "S"} THIS TURN
      </span>
      <Sep />
      <span>
        LAST:{" "}
        <span style={{ color: lastToolName ? "var(--color-text-dim)" : "var(--color-text-faint)" }}>
          {lastToolName ?? "—"}
        </span>
      </span>
      <Sep />
      <span
        className="flex-1 overflow-hidden whitespace-nowrap"
        style={{
          color: sending ? "var(--color-accent)" : "var(--color-border-bright)",
          letterSpacing: 0,
          textShadow: sending ? "0 0 8px var(--color-accent-glow)" : "none",
        }}
      >
        {bars}
      </span>
      <Sep />
      <span style={{ color: sending ? "var(--color-amber)" : "var(--color-accent)" }}>
        ● {sending ? "WORKING" : "ALIVE"}
      </span>
    </div>
  );
}

function Sep() {
  return <span style={{ color: "var(--color-border-strong)" }}>──</span>;
}

const BAR_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇"];
const N_BARS = 56;

function useHeartbeatBars(sending: boolean): string {
  const [s, setS] = useState(() => "▁".repeat(N_BARS));
  const phase = useRef(0);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (t: number) => {
      const dt = t - last;
      if (dt > (sending ? 90 : 200)) {
        last = t;
        phase.current += 1;
        setS(buildPulse(phase.current, sending));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [sending]);

  return s;
}

function buildPulse(phase: number, sending: boolean): string {
  // A pseudo-heartbeat: mostly baseline `▁`, with periodic 3-bar
  // spikes at fixed positions, drifting one step left each tick.
  const out: string[] = new Array(N_BARS).fill("▁");
  const spikeEvery = sending ? 7 : 14;
  const amp = sending ? 6 : 3;
  for (let i = 0; i < N_BARS; i++) {
    const local = (i + phase) % spikeEvery;
    if (local === 0) out[i] = BAR_CHARS[Math.min(amp, BAR_CHARS.length - 1)];
    else if (local === 1) out[i] = BAR_CHARS[Math.min(amp - 2, BAR_CHARS.length - 1)];
    else if (local === spikeEvery - 1) out[i] = BAR_CHARS[Math.max(0, amp - 3)];
  }
  return out.join("");
}
