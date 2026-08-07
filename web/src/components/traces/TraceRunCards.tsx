import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentReplayTool, AgentReplayTurn } from "@/lib/types";
import { Tooltip } from "@/components/ui/Tooltip";

export function TraceRunCards() {
  const [runs, setRuns] = useState<AgentReplayTurn[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.agentReplay(3).then((next) => {
      if (alive) setRuns(next);
    }).catch(() => undefined);
    load();
    const t = setInterval(load, 15_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (runs.length === 0) return null;

  return (
    <section className="trace-run-section mt-8 mb-5">
      <div className="trace-run-heading">
        <div>
          <div className="brut-meta" style={{ color: "var(--color-text-muted)" }}>── run inspector</div>
        </div>
        <span className="trace-run-count">{runs.length.toString().padStart(2, "0")}</span>
      </div>
      <div className="trace-run-grid">
        {runs.map((run) => (
          <RunCard
            key={`${run.id}-${run.started_at ?? ""}`}
            run={run}
            open={open === run.id}
            onToggle={() => setOpen(open === run.id ? null : run.id)}
          />
        ))}
      </div>
    </section>
  );
}

function RunCard({ run, open, onToggle }: { run: AgentReplayTurn; open: boolean; onToggle: () => void }) {
  // A tool failure only fails the RUN if it wasn't recovered — i.e. no later
  // call of the same tool succeeded. The agent's self-correction (a tool errors,
  // it fixes the args and retries successfully) should read as "ok", not
  // "failure". The per-tool status below still shows the individual error.
  const failed = run.tools.filter((t, i) =>
    t.status === "failure" &&
    !run.tools.slice(i + 1).some((later) => later.name === t.name && later.status === "success"),
  ).length;
  const blocked = run.tools.filter((t) => t.status === "blocked").length;
  const model = run.models[run.models.length - 1]?.model ?? "no model";
  // ¢ prefix like every other cost in the app (budget line, spend cell) —
  // this card previously invented its own "0.123c"/"$0" formats.
  const cost = run.cost_cents > 0 ? `¢${run.cost_cents.toFixed(2)}` : "¢0";
  const tokens = (run.input_tokens ?? 0) + (run.output_tokens ?? 0);
  const durationS = run.started_at && run.ended_at
    ? Math.max(0, Math.round((new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()) / 1000))
    : null;
  const tone = failed ? "var(--color-danger)" : blocked ? "var(--color-amber)" : "var(--color-accent)";
  const title = runTitle(run);

  return (
    <article className="trace-run-card" data-open={open}>
      <button
        type="button"
        onClick={onToggle}
        className="trace-run-card-button"
        style={{ "--run-tone": tone } as React.CSSProperties}
      >
        <div className="trace-run-status-line">
          <span className="trace-run-service">{run.service || "unknown"}</span>
          <span className="trace-run-state" style={{ color: tone }}>{failed ? "failure" : blocked ? "blocked" : "ok"}</span>
        </div>
        <Tooltip text={run.user} placement="top"><div className="trace-run-title">{title}</div></Tooltip>
        <Tooltip text={model} placement="top"><div className="trace-run-meta">{model}</div></Tooltip>
        <div className="trace-run-chips">
          <Chip label="calls" value={String(run.models.length)} />
          <Chip label="tools" value={String(run.tools.length)} tone={failed ? "danger" : blocked ? "warn" : "default"} />
          <Chip label="guards" value={String(run.guards.length)} />
          <Chip
            label="tok"
            value={fmtTokens(tokens)}
            tip={`${run.input_tokens.toLocaleString()} in · ${run.output_tokens.toLocaleString()} out · ${run.cached_tokens.toLocaleString()} cached`}
          />
          <Chip label="cost" value={cost} />
        </div>
        <div className="trace-run-card-footer">
          <span>
            {startedAt(run.started_at)}
            {durationS !== null && ` · ${durationS >= 60 ? `${Math.floor(durationS / 60)}m ${durationS % 60}s` : `${durationS}s`}`}
          </span>
          <span>{open ? "collapse" : "inspect"}</span>
        </div>
      </button>
      {open && (
        <div className="trace-run-detail">
          {run.tools.length > 0 ? (
            <TraceSection label="tool sequence">
              <div className="trace-tool-list">
                {run.tools.map((tool, i) => <ToolBlock key={`${tool.name}-${i}`} index={i + 1} tool={tool} />)}
              </div>
            </TraceSection>
          ) : (
            <div className="trace-empty-note">No tools were called in this run.</div>
          )}
          {run.guards.length > 0 && (
            <TraceSection label="guard decisions">
              <div className="trace-guard-note">
                {run.guards.map((g) => `${g.event}${g.result ? `: ${g.result}` : ""}`).join(" · ")}
              </div>
            </TraceSection>
          )}
          {run.assistant && (
            <TraceSection label="final output">
              <pre className="trace-run-output">{run.assistant}</pre>
            </TraceSection>
          )}
        </div>
      )}
    </article>
  );
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function TraceSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="trace-detail-section">
      <div className="trace-detail-label">── {label}</div>
      {children}
    </section>
  );
}

function runTitle(run: AgentReplayTurn): string {
  const raw = (run.user || "").trim();
  if (!raw) return "Autonomous run";
  if (/scheduled heartbeat tick/i.test(raw)) return "Scheduled heartbeat tick";
  if (/daily reflection tick/i.test(raw)) return "Daily reflection tick";
  return raw.length > 92 ? `${raw.slice(0, 92).trim()}...` : raw;
}

function Chip({ label, value, tone = "default", tip }: { label: string; value: string; tone?: "default" | "warn" | "danger"; tip?: string }) {
  // The tooltip trigger lives INSIDE the chip: .trace-run-chip must stay
  // a direct grid child of .trace-run-chips or the row layout shatters.
  const body = tip ? (
    <Tooltip text={tip} placement="top"><span>{label}</span></Tooltip>
  ) : (
    <span>{label}</span>
  );
  return (
    <span className="trace-run-chip" data-tone={tone}>
      {body}
      <b>{value}</b>
    </span>
  );
}

function startedAt(value: string | null): string {
  if (!value) return "no timestamp";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function ToolBlock({ tool, index }: { tool: AgentReplayTool; index: number }) {
  return (
    <div className="trace-tool-block">
      <div className="trace-tool-head">
        <span className="trace-tool-step" style={{ color: statusColor(tool.status) }}>
          {String(index).padStart(2, "0")} · {tool.status}
        </span>
        <span>{tool.name}</span>
      </div>
      {(tool.args || tool.result) && (
        <div className="trace-tool-payloads">
          {tool.args && (
            <div className="trace-tool-payload">
              <div className="trace-tool-payload-label">args</div>
              <pre>{tool.args}</pre>
            </div>
          )}
          {tool.result && (
            <div className="trace-tool-payload">
              <div className="trace-tool-payload-label">result</div>
              <pre>{tool.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function statusColor(status: AgentReplayTool["status"]): string {
  if (status === "success") return "var(--color-accent)";
  if (status === "blocked") return "var(--color-amber)";
  if (status === "failure") return "var(--color-danger)";
  return "var(--color-text-muted)";
}
