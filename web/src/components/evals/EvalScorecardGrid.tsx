import { Tooltip } from "@/components/ui/Tooltip";
import type { EvalScorecard, EvalTrend } from "@/lib/types";

// Status tokens the app already defines and validates (index.css) — a
// trend is a STATE, not a series, so it gets one reserved color +
// icon + label, never a generated hue. Same non-negotiable the trace
// cards already follow for tool-call status.
const TREND_META: Record<EvalTrend, { icon: string; label: string; tone: string }> = {
  improving:          { icon: "▲", label: "improving",  tone: "var(--color-success)" },
  steady:             { icon: "■", label: "steady",     tone: "var(--color-text-dim)" },
  degrading:          { icon: "▼", label: "degrading",  tone: "var(--color-danger)" },
  insufficient_data:  { icon: "·", label: "new",         tone: "var(--color-text-faint)" },
};

const CONTRACT_LABEL: Record<EvalScorecard["contract_kind"], string> = {
  states: "state machine",
  requires_tools: "required tools",
  none: "no contract",
};

function fmt(n: number | null, digits = 2): string {
  return n === null ? "—" : n.toFixed(digits);
}

export function EvalScorecardGrid({
  cards, view,
}: {
  cards: [string, EvalScorecard][];
  view: "cards" | "table";
}) {
  if (cards.length === 0) return null;

  if (view === "table") {
    return (
      <div className="eval-table-wrap">
        <table className="eval-table">
          <thead>
            <tr>
              <th>skill</th><th>contract</th><th>runs</th><th>compliance</th>
              <th>avg violations</th><th>avg guard fires</th><th>avg cost</th><th>trend</th>
            </tr>
          </thead>
          <tbody>
            {cards.map(([taskId, card]) => {
              const trend = TREND_META[card.trend];
              return (
                <tr key={taskId}>
                  <td>{taskId}</td>
                  <td>{CONTRACT_LABEL[card.contract_kind]}</td>
                  <td>{card.runs}</td>
                  <td>{card.compliance_rate === null ? "—" : `${Math.round(card.compliance_rate * 100)}%`}</td>
                  <td>{fmt(card.avg_violations)}</td>
                  <td>{fmt(card.avg_guard_fires)}</td>
                  <td>{card.avg_cost_cents === null ? "—" : `¢${card.avg_cost_cents.toFixed(2)}`}</td>
                  <td style={{ color: trend.tone }}>{trend.icon} {trend.label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="eval-grid">
      {cards.map(([taskId, card]) => <EvalCard key={taskId} taskId={taskId} card={card} />)}
    </div>
  );
}

function EvalCard({ taskId, card }: { taskId: string; card: EvalScorecard }) {
  const trend = TREND_META[card.trend];
  const compliancePct = card.compliance_rate === null ? null : Math.round(card.compliance_rate * 100);
  const compliantRuns = card.compliance_rate === null ? null : Math.round(card.compliance_rate * card.runs);

  return (
    <div className="eval-card" style={{ ["--card-tone" as string]: trend.tone }}>
      <div className="eval-card-head">
        <Tooltip text={taskId} placement="top">
          <div className="eval-card-title">{taskId}</div>
        </Tooltip>
        <Tooltip text={`Trend over recent runs: ${trend.label}`} placement="top">
          <span className="eval-trend-chip" style={{ color: trend.tone, borderColor: trend.tone }}>
            {trend.icon} {trend.label}
          </span>
        </Tooltip>
      </div>

      <div className="eval-card-meta">{CONTRACT_LABEL[card.contract_kind]} · {card.runs} run{card.runs === 1 ? "" : "s"}</div>

      <Tooltip
        text={compliantRuns === null ? "No runs scored yet" : `${compliantRuns}/${card.runs} runs matched the skill's contract`}
        placement="top"
      >
        <div className="eval-meter" role="meter" aria-valuenow={compliancePct ?? 0} aria-valuemin={0} aria-valuemax={100}>
          <div className="eval-meter-track">
            <div className="eval-meter-fill" style={{ width: `${compliancePct ?? 0}%` }} />
          </div>
          <span className="eval-meter-label">{compliancePct === null ? "—" : `${compliancePct}%`} compliant</span>
        </div>
      </Tooltip>

      <div className="eval-chips">
        <EvalChip label="viol" value={fmt(card.avg_violations, 1)} warn={(card.avg_violations ?? 0) > 0.5} />
        <EvalChip label="guard" value={fmt(card.avg_guard_fires, 1)} warn={(card.avg_guard_fires ?? 0) > 1} />
        <EvalChip label="¢/run" value={card.avg_cost_cents === null ? "—" : card.avg_cost_cents.toFixed(2)} />
      </div>

      <ModelBreakdown byModel={card.by_model} />
    </div>
  );
}

// Only renders once a skill has actually lived through more than one
// model — a single-model skill has nothing to compare yet, and showing
// a one-row "breakdown" would just be noise.
function ModelBreakdown({ byModel }: { byModel: EvalScorecard["by_model"] }) {
  const rows = Object.entries(byModel);
  if (rows.length < 2) return null;
  return (
    <div className="eval-model-rows">
      {rows.map(([model, slice_]) => (
        <div className="eval-model-row" key={model}>
          <Tooltip text={model} placement="top">
            <span className="eval-model-name">{model}</span>
          </Tooltip>
          <span className="eval-model-stat">
            {slice_.compliance_rate === null ? "—" : `${Math.round(slice_.compliance_rate * 100)}%`}
            <span className="eval-model-stat-dim"> · {slice_.runs} run{slice_.runs === 1 ? "" : "s"}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function EvalChip({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <span className="eval-chip" data-tone={warn ? "warn" : "default"}>
      {label}
      <b>{value}</b>
    </span>
  );
}
