import { useEffect, useState } from "react";
import { api, parseServerIso } from "@/lib/api";

interface Upcoming {
  next_tick: string | null;
  default_interval_min: number;
  next_task: { id: string; title: string; due_at: string; recurrence: string } | null;
}

/** "What the agent is about to do." Surfaces the autonomy in a way the
 *  user can feel: a live countdown to the next heartbeat tick + the
 *  earliest scheduled task. Polls /agent/upcoming every 10s, updates
 *  the visible countdown every second. */
export function UpcomingPanel() {
  const [data, setData] = useState<Upcoming | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const fetchOnce = () => api.agentUpcoming().then(setData).catch(() => undefined);
    fetchOnce();
    const slow = setInterval(fetchOnce, 10_000);
    const fast = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(slow); clearInterval(fast); };
  }, []);

  if (!data) return null;

  const nextTickMs = data.next_tick ? parseIsoLocal(data.next_tick) : null;
  const nextTaskMs = data.next_task ? parseIsoLocal(data.next_task.due_at) : null;

  // Choose the earliest concrete event the agent is going to do next.
  const candidates: { ms: number; label: string; sub: string }[] = [];
  if (nextTickMs) {
    candidates.push({
      ms: nextTickMs,
      label: "scheduled heartbeat",
      sub: `${data.default_interval_min}min default`,
    });
  }
  if (data.next_task && nextTaskMs) {
    candidates.push({
      ms: nextTaskMs,
      label: data.next_task.title.toLowerCase(),
      sub: data.next_task.recurrence === "none" ? "one-shot" : data.next_task.recurrence,
    });
  }
  candidates.sort((a, b) => a.ms - b.ms);
  const target = candidates[0];

  return (
    <div className="p-6" style={{ border: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}>
      <div className="flex items-baseline justify-between mb-3">
        <span className="text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-text-muted)" }}>
          ── next fire
        </span>
        <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-faint)" }}>
          autonomous
        </span>
      </div>
      {target ? (
        <>
          <div
            className="leading-[0.85] my-1"
            style={{
              color: "var(--color-accent)",
              fontSize: "clamp(40px, 6vw, 64px)",
              fontVariantNumeric: "tabular-nums",
              letterSpacing: "-0.03em",
              fontWeight: 700,
              textShadow: "0 0 18px var(--color-accent-glow)",
            }}
          >
            {formatCountdown(target.ms - now)}
          </div>
          <div className="mt-3 text-[13px]" style={{ color: "var(--color-text)" }}>
            <span style={{ color: "var(--color-accent)" }}>›</span>{" "}
            {target.label}
          </div>
          <div
            className="mt-1 text-[10px] uppercase tracking-[0.16em]"
            style={{ color: "var(--color-text-faint)" }}
          >
            {target.sub} · {fmtAbs(new Date(target.ms))}
          </div>
        </>
      ) : (
        <div className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>
          ─ nothing scheduled · heartbeat will tick on default interval ─
        </div>
      )}
    </div>
  );
}

function parseIsoLocal(iso: string): number {
  return parseServerIso(iso);
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "00:00";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  }
  return `${pad(m)}:${pad(sec)}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function fmtAbs(d: Date): string {
  const dd = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const tt = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${dd} · ${tt}`.toLowerCase();
}
