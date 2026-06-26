import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentReplayTool, AgentReplayTurn } from "@/lib/types";

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
  const failed = run.tools.filter((t) => t.status === "failure").length;
  const blocked = run.tools.filter((t) => t.status === "blocked").length;
  const model = run.models[run.models.length - 1]?.model ?? "no model";
  const cost = run.cost_cents > 0 ? `${run.cost_cents.toFixed(3)}c` : "$0";
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
        <div className="trace-run-title" title={run.user}>{title}</div>
        <div className="trace-run-meta" title={model}>{model}</div>
        <div className="trace-run-chips">
          <Chip label="calls" value={String(run.models.length)} />
          <Chip label="tools" value={String(run.tools.length)} tone={failed ? "danger" : blocked ? "warn" : "default"} />
          <Chip label="guards" value={String(run.guards.length)} />
          <Chip label="cost" value={cost} />
        </div>
        <div className="trace-run-card-footer">
          <span>{startedAt(run.started_at)}</span>
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

function Chip({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "warn" | "danger" }) {
  return (
    <span className="trace-run-chip" data-tone={tone}>
      <span>{label}</span>
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
        <span title={tool.name}>{tool.name}</span>
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
