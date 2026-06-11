import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatCents } from "@/lib/format";
import type { StatusMap } from "@/lib/types";
import { Tooltip } from "@/components/ui/Tooltip";

/**
 * SidebarTelemetry — ambient operator readouts that fill the previously
 * empty middle column of the sidebar. Designed as an instrument-cluster
 * readout, not a card: tiny mono type, no borders inside, single column,
 * hairline divider on top.
 *
 * Surfaces four signals at a glance:
 *   • next autonomous fire (compact countdown)
 *   • today's token spend as a bar (proxy for $ used vs budget)
 *   • last heartbeat tick (age)
 *   • current model + provider host (truncated)
 *
 * Polls every 30s. All endpoints already exist; this just composes
 * the small set the user looks at most often without leaving any page.
 */
interface Upcoming {
  next_tick?: string | null;
  default_interval_min?: number;
  next_task?: { title: string; due_at: string } | null;
}
interface Stats { cost_cents: number; budget_cents: number; input_tokens: number; output_tokens: number }
export function SidebarTelemetry() {
  const [upcoming, setUpcoming] = useState<Upcoming | null>(null);
  const [status, setStatus] = useState<StatusMap | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [model, setModel] = useState<{ model: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const load = () => {
      api.agentUpcoming().then(setUpcoming).catch(() => undefined);
      api.status().then(setStatus).catch(() => undefined);
      api.statsToday().then(setStats).catch(() => undefined);
      // contextGauge returns { used_tokens, limit_tokens, model, pct } —
      // we only need the model name here.
      api.contextGauge().then((c) => setModel({ model: c.model })).catch(() => undefined);
    };
    load();
    const dataTimer = setInterval(load, 30_000);
    const tickTimer = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(dataTimer); clearInterval(tickTimer); };
  }, []);

  // ── compute display values ──
  const nextMs = upcoming?.next_tick ? new Date(upcoming.next_tick).getTime() : null;
  const untilSec = nextMs !== null ? Math.max(0, Math.floor((nextMs - now) / 1000)) : null;
  const untilLabel = untilSec === null
    ? "—"
    : untilSec < 60
      ? `${untilSec}s`
      : untilSec < 3600
        ? `${Math.floor(untilSec / 60)}m`
        : `${Math.floor(untilSec / 3600)}h ${Math.floor((untilSec % 3600) / 60)}m`;

  const heartbeatAge = status?.heartbeat?.age_s ?? null;
  const heartbeatLabel = heartbeatAge === null
    ? "—"
    : heartbeatAge < 60
      ? `${heartbeatAge}s ago`
      : heartbeatAge < 3600
        ? `${Math.floor(heartbeatAge / 60)}m ago`
        : `${Math.floor(heartbeatAge / 3600)}h ago`;

  const budget = stats?.budget_cents ?? 17;
  const spent = stats?.cost_cents ?? 0;
  const spentPct = Math.min(100, Math.max(0, (spent / Math.max(1, budget)) * 100));
  const spendTone = spentPct >= 80 ? "var(--color-warning)" : "var(--color-accent)";

  const modelShort = model?.model ? compactModelName(model.model) : "—";

  return (
    <div
      className="px-3 pt-3 pb-2 flex flex-col gap-3"
      style={{
        borderTop: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        letterSpacing: "0.10em",
        color: "var(--color-text-faint)",
      }}
    >
      {/* Next autonomous fire */}
      <Tooltip text={<>Countdown to the next autonomous run. Includes the next scheduled task and the regular heartbeat interval (~60 min by default).</>} placement="top">
        <div className="hm-info hm-info--bare">
          <Row label="next fire" value={untilLabel} valueColor={untilSec !== null && untilSec < 60 ? "var(--color-warning)" : "var(--color-text-dim)"} />
        </div>
      </Tooltip>

      {/* Heartbeat liveness */}
      <Tooltip text={<>Time since the last heartbeat tick was observed. Tick runs every {upcoming?.default_interval_min ?? 60} min and on every due task.</>} placement="top">
        <div className="hm-info hm-info--bare">
          <Row
            label="heartbeat"
            value={heartbeatLabel}
            valueColor={status?.heartbeat?.state === "live"
              ? "var(--color-accent)"
              : status?.heartbeat?.state === "idle"
                ? "var(--color-text-muted)"
                : "var(--color-warning)"}
          />
        </div>
      </Tooltip>

      {/* Budget bar */}
      <Tooltip text={<>Estimated LLM spend today, in cents. Free-tier providers don't count. Bar turns amber at 80% of the configured daily budget.</>} placement="top">
        <div className="hm-info hm-info--bare">
          <div className="flex justify-between mb-1">
            <span>budget</span>
            <span style={{ color: "var(--color-text-muted)" }}>
              {formatCents(spent)} / {formatCents(budget)}
            </span>
          </div>
          <div style={{ height: 2, background: "var(--color-border)", overflow: "hidden" }}>
            <div style={{
              width: `${spentPct}%`,
              height: "100%",
              background: spendTone,
              transition: "width 400ms ease",
            }} />
          </div>
        </div>
      </Tooltip>

      {/* Current model */}
      <Tooltip
        text={<><strong>{model?.model ?? "no model yet"}</strong><br />Last LLM model used. Swap with <kbd>/use &lt;model&gt;</kbd> in chat.</>}
        placement="top"
      >
        <div className={model ? "hm-info hm-info--bare" : ""}>
          <div>model</div>
          <div className="mt-1" style={{ color: "var(--color-text-muted)", letterSpacing: "0.06em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {modelShort}
          </div>
        </div>
      </Tooltip>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span>{label}</span>
      <span style={{ color: valueColor, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </span>
    </div>
  );
}

// Trim "openai/gpt-oss-120b:free" → "gpt-oss-120b", "gemini-2.5-flash" → "gemini-2.5-flash"
function compactModelName(name: string): string {
  // strip vendor prefix
  let s = name.includes("/") ? name.split("/").pop()! : name;
  // strip :free / :nitro suffix
  s = s.replace(/:[a-z]+$/i, "");
  return s;
}
