import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface TodayStats {
  events: number;
  unique_tools: number;
  tasks_fired: number;
  memory_writes: number;
  memory_forgets: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_cents: number;
}

/** Activity since UTC midnight — window is server-authoritative. */
export function GrowthDeltas() {
  const [stats, setStats] = useState<TodayStats | null>(null);

  useEffect(() => {
    const fetch = () =>
      api.statsToday().then(setStats).catch(() => undefined);

    fetch();
    const id = setInterval(fetch, 30_000);
    return () => clearInterval(id);
  }, []);

  if (!stats) return null;

  const memDelta = stats.memory_writes - stats.memory_forgets;
  const costCents = stats.cost_cents ?? 0;
  const totalTokens = (stats.input_tokens ?? 0) + (stats.output_tokens ?? 0);

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

      <Delta n={stats.events} label="events" />
      <Sep />
      <Delta n={stats.unique_tools} label="tools" suffix=" used" />
      <Sep />
      <Delta n={stats.tasks_fired} label="tasks fired" />
      <Sep />
      <Delta
        n={Math.abs(memDelta)}
        label={memDelta >= 0 ? "memories written" : "memories pruned"}
        positive={memDelta >= 0}
      />
      {totalTokens > 0 && (
        <>
          <Sep />
          <TokenCost tokens={totalTokens} costCents={costCents} />
        </>
      )}
    </div>
  );
}

function Sep() {
  return <span style={{ color: "var(--color-border-strong)" }}>──</span>;
}

function Delta({
  n, label, suffix, positive = true,
}: { n: number; label: string; suffix?: string; positive?: boolean }) {
  const active = n > 0;
  const color = active
    ? positive ? "var(--color-accent)" : "var(--color-amber)"
    : "var(--color-text-faint)";
  return (
    <span className="flex items-baseline gap-1.5 text-[11px] uppercase tracking-[0.12em]">
      <span style={{ color, fontSize: 15, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}>
        {active && !positive ? "−" : "+"}{n.toString().padStart(2, "0")}
      </span>
      <span style={{ color: "var(--color-text-muted)" }}>
        {label}{suffix ?? ""}
      </span>
    </span>
  );
}

function TokenCost({ tokens, costCents }: { tokens: number; costCents: number }) {
  const tokStr = tokens >= 1000
    ? `${(tokens / 1000).toFixed(1)}k`
    : tokens.toString();
  const costStr = costCents >= 100
    ? `$${(costCents / 100).toFixed(2)}`
    : `¢${costCents.toFixed(1)}`;
  return (
    <span className="flex items-baseline gap-1.5 text-[11px] uppercase tracking-[0.12em]">
      <span style={{ color: "var(--color-amber)", fontSize: 15, letterSpacing: "-0.02em" }}>
        {tokStr}
      </span>
      <span style={{ color: "var(--color-text-muted)" }}>
        tok · {costStr}
      </span>
    </span>
  );
}
