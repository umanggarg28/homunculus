import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAgentPaused } from "@/hooks/useAgentPaused";
import type { ContainmentStatus } from "@/lib/types";

/** Containment protocols — the agent's real guardrails, worn like a
 *  reactor status board. Every line is live config, never fiction:
 *  if a guard is off (dev mode, env override), the row says BREACH in
 *  red. The drama is in the framing; the data is the truth.
 */
export function ContainmentPanel() {
  const [status, setStatus] = useState<ContainmentStatus | null>(null);
  const paused = useAgentPaused();

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.containment()
        .then((s) => { if (!cancelled) setStatus(s); })
        .catch(() => undefined);
    load();
    const t = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (!status) return null;

  const rows: { label: string; value: string; ok: boolean }[] = [
    {
      label: "docker socket",
      value: status.docker_proxy ? "proxied · exec denied" : "RAW SOCKET",
      ok: status.docker_proxy,
    },
    {
      label: "network egress",
      value: status.ssrf_guard ? "public ips only" : "PRIVATE NET OPEN",
      ok: status.ssrf_guard,
    },
    {
      label: "spend ceiling",
      value: status.daily_budget_cents > 0
        ? `${(status.daily_budget_cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" })}/day enforced`
        : "UNCAPPED",
      ok: status.daily_budget_cents > 0,
    },
    {
      label: "step limit",
      value: `${status.max_steps} tool calls / tick`,
      ok: true,
    },
    {
      label: "delivery gate",
      value: status.delivery_gate ? "criteria-checked" : "OPEN",
      ok: status.delivery_gate,
    },
    {
      label: "mode",
      value: status.mode === "plan" ? "plan · mutations refused" : "build · full authority",
      ok: true,
    },
  ];

  const breaches = rows.filter((r) => !r.ok).length;
  const headline = paused
    ? "HALTED BY OPERATOR"
    : breaches > 0
      ? `${breaches} PROTOCOL${breaches > 1 ? "S" : ""} DOWN`
      : "ALL PROTOCOLS HOLDING";
  const headColor = paused || breaches > 0 ? "var(--color-danger)" : "var(--color-accent)";

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── containment · autonomous unit restraints</span>
        <span style={{ color: headColor, textShadow: `0 0 8px ${headColor}`, letterSpacing: "0.14em" }}>
          {headline}
        </span>
      </div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
        }}
      >
        {rows.map((r) => (
          <div
            key={r.label}
            className="px-4 py-3 flex items-baseline justify-between gap-3"
            style={{ borderTop: "1px solid var(--color-border)" }}
          >
            <span className="brut-label" style={{ color: "var(--color-text-muted)", letterSpacing: "0.14em" }}>
              {r.label}
            </span>
            <span
              className="brut-label"
              style={{
                color: r.ok ? "var(--color-text)" : "var(--color-danger)",
                textShadow: r.ok ? "none" : "0 0 8px var(--color-danger)",
                letterSpacing: "0.1em",
                textAlign: "right",
              }}
            >
              {r.ok ? "●" : "▲"} {r.value}
            </span>
          </div>
        ))}
      </div>
      <div
        className="brut-meta px-4 py-2 flex justify-between"
        style={{ color: "var(--color-text-faint)", borderTop: "1px solid var(--color-border)" }}
      >
        <span>refusals issued (recent window)</span>
        <span style={{ fontVariantNumeric: "tabular-nums", color: status.blocked_recent > 0 ? "var(--color-text)" : "var(--color-text-faint)" }}>
          {String(status.blocked_recent).padStart(3, "0")}
        </span>
      </div>
    </div>
  );
}
