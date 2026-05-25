import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

/** "What changed since yesterday." A signature Hermes move — surfaces
 *  the agent's growth over time as a single line of `+N MEMORIES ·
 *  +N SKILLS USED · N TASKS FIRED`. Makes the agent feel like it
 *  accumulates, not just runs. */
export function GrowthDeltas() {
  const { events } = useEventStream(800);
  const [memCount, setMemCount] = useState<{ today: number; yesterday: number } | null>(null);

  useEffect(() => {
    api.memoryList().then((entries) => {
      const startOfToday = startOfDay();
      const startOfYesterday = startOfToday - 24 * 3600 * 1000;
      let today = 0, yesterday = 0;
      for (const e of entries) {
        const ms = e.mtime * 1000;
        if (ms >= startOfToday) today += 1;
        else if (ms >= startOfYesterday) yesterday += 1;
      }
      setMemCount({ today, yesterday });
    }).catch(() => undefined);
  }, []);

  if (!memCount) return null;

  const todayMs = startOfDay();
  const todayEvents = events.filter((e) => new Date(e.ts).getTime() >= todayMs);
  const toolsToday = new Set(
    todayEvents
      .filter((e) => e.event === "tool_call")
      .map((e) => e.name)
      .filter(Boolean) as string[]
  );
  const tasksFired = todayEvents.filter(
    (e) => e.event === "tool_call" && e.name === "complete_task"
  ).length;

  return (
    <div
      className="flex items-baseline gap-5 px-5 py-3 mb-6 flex-wrap"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-surface-1)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <span
        className="text-[10px] uppercase tracking-[0.22em] shrink-0"
        style={{ color: "var(--color-text-muted)" }}
      >
        ── since midnight
      </span>
      <Delta n={memCount.today} label="memories" prev={memCount.yesterday} />
      <Sep />
      <Delta n={toolsToday.size} label="unique tools" suffix=" used" />
      <Sep />
      <Delta n={tasksFired} label="tasks fired" />
      <Sep />
      <Delta n={todayEvents.length} label="events" />
    </div>
  );
}

function Sep() {
  return <span style={{ color: "var(--color-border-strong)" }}>──</span>;
}

function Delta({
  n, label, prev, suffix,
}: { n: number; label: string; prev?: number; suffix?: string }) {
  const isUp = prev !== undefined && n > prev;
  const diff = prev !== undefined ? n - prev : null;
  return (
    <span className="flex items-baseline gap-1.5 text-[11px] uppercase tracking-[0.12em]">
      <span
        style={{
          color: n > 0 ? "var(--color-accent)" : "var(--color-text-muted)",
          fontSize: 15,
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.02em",
        }}
      >
        +{n.toString().padStart(2, "0")}
      </span>
      <span style={{ color: "var(--color-text-muted)" }}>
        {label}{suffix ?? ""}
      </span>
      {diff !== null && diff !== 0 && (
        <span
          className="text-[9px]"
          style={{ color: isUp ? "var(--color-accent)" : "var(--color-text-faint)" }}
        >
          {isUp ? "▲" : "▼"}{Math.abs(diff)} vs yesterday
        </span>
      )}
    </span>
  );
}

function startOfDay(): number {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}
