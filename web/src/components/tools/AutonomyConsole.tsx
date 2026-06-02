import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import type { AgentControls, AgentReplayTool, AgentReplayTurn, Skill } from "@/lib/types";

interface Props {
  controls: AgentControls;
  skills: Skill[];
  replay: AgentReplayTurn[];
  onControlsChange: (next: AgentControls) => void;
  onReplayChange: (next: AgentReplayTurn[]) => void;
}

export function AutonomyConsole({ controls, skills, replay, onControlsChange, onReplayChange }: Props) {
  const [draft, setDraft] = useState(controls);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [openTurn, setOpenTurn] = useState<string | null>(replay[0]?.id ?? null);
  const [didPrimeOpenTurn, setDidPrimeOpenTurn] = useState(Boolean(replay[0]));
  const toolNames = useMemo(() => skills.map((s) => s.name).sort(), [skills]);

  useEffect(() => setDraft(controls), [controls]);
  useEffect(() => {
    if (replay.length === 0) {
      if (openTurn) setOpenTurn(null);
      return;
    }
    if (!didPrimeOpenTurn) {
      setOpenTurn(replay[0].id);
      setDidPrimeOpenTurn(true);
      return;
    }
    if (openTurn && !replay.some((turn) => turn.id === openTurn)) {
      setOpenTurn(replay[0].id);
    }
  }, [didPrimeOpenTurn, openTurn, replay]);

  const save = async (patch: Partial<AgentControls>) => {
    setSaving(true);
    try {
      const next = await api.agentControlsUpdate(patch);
      onControlsChange(next);
      setDraft(next);
    } finally {
      setSaving(false);
    }
  };

  const saveLists = () => save({
    allowed_tools: normalizeToolList(draft.allowed_tools, toolNames),
    blocked_tools: normalizeToolList(draft.blocked_tools, toolNames),
  });

  const refreshReplay = async () => {
    setRefreshing(true);
    try {
      const next = await api.agentReplay(8);
      onReplayChange(next);
      setOpenTurn(next[0]?.id ?? null);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <section className="mb-8" style={{ fontFamily: "var(--font-mono)" }}>
      <style>{`
        .autonomy-panel-grid {
          display: grid;
          grid-template-columns: minmax(360px, 0.82fr) minmax(420px, 1.18fr);
          gap: 18px;
          align-items: start;
        }
        .autonomy-card {
          border: 1px solid var(--color-border);
          background: linear-gradient(180deg, rgba(119,255,61,0.018), transparent), var(--color-surface-1);
          min-width: 0;
          overflow: hidden;
        }
        .autonomy-card-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 18px;
          padding: 20px 24px;
          border-bottom: 1px solid var(--color-border);
        }
        .autonomy-pane-pad {
          padding: 20px 24px;
        }
        .autonomy-control-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 150px;
          gap: 20px;
          align-items: center;
          border-top: 1px solid var(--color-border);
          padding: 18px 24px;
        }
        .autonomy-control-row:first-child {
          border-top: none;
        }
        .autonomy-toggle {
          border: 1px solid var(--color-border);
          background: transparent;
          color: var(--color-text-muted);
          height: 34px;
          min-width: 74px;
          padding: 0 12px;
          text-transform: uppercase;
          letter-spacing: 0.12em;
          font-size: 10px;
        }
        .autonomy-control-widget {
          display: flex;
          justify-content: flex-end;
          width: 150px;
        }
        .autonomy-control-widget > .autonomy-toggle,
        .autonomy-control-widget > input {
          width: 100%;
        }
        .autonomy-segment {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          width: 150px;
        }
        .autonomy-toggle[data-active="true"] {
          border-color: var(--color-border-bright);
          color: var(--color-accent);
          background: rgba(124,254,0,0.08);
        }
        .autonomy-list-input {
          width: 100%;
          min-height: 74px;
          resize: vertical;
          border: 1px solid var(--color-border);
          background: var(--color-surface-1);
          color: var(--color-text);
          padding: 12px 14px;
          font-size: 11px;
          line-height: 1.55;
          outline: none;
        }
        .autonomy-replay-row {
          border-top: 1px solid var(--color-border);
          min-width: 0;
        }
        .autonomy-replay-row:first-of-type {
          border-top: none;
        }
        .autonomy-replay-row[data-open="true"] { background: rgba(124,254,0,0.035); }
        .autonomy-replay-summary {
          width: 100%;
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          padding: 18px 24px;
          border: none;
          background: transparent;
          color: inherit;
          text-align: left;
          cursor: pointer;
          font-family: var(--font-mono);
        }
        .autonomy-replay-body {
          padding: 0 24px 18px;
        }
        .autonomy-panel-grid,
        .autonomy-panel-grid * {
          min-width: 0;
        }
        .autonomy-pre {
          white-space: pre-wrap;
          overflow-wrap: anywhere;
          word-break: break-word;
          max-width: 100%;
        }
        @media (max-width: 940px) {
          .autonomy-panel-grid { grid-template-columns: 1fr; }
        }
        @media (max-width: 520px) {
          .autonomy-card-head {
            padding: 16px;
          }
          .autonomy-control-row {
            grid-template-columns: 1fr;
            padding: 16px;
          }
          .autonomy-pane-pad,
          .autonomy-replay-summary,
          .autonomy-replay-body {
            padding-left: 16px;
            padding-right: 16px;
          }
          .autonomy-toggle {
            width: 100%;
          }
          .autonomy-control-widget,
          .autonomy-segment {
            width: 100%;
          }
        }
      `}</style>

      <div className="autonomy-panel-grid">
        <div className="autonomy-card">
          <div className="autonomy-card-head">
            <div className="min-w-0">
              <div className="hm-rail-label">autonomy console</div>
              <div className="mt-1 text-[12px]" style={{ color: "var(--color-text-dim)" }}>
                tool permissions · dry-run · max steps
              </div>
            </div>
          </div>
          <ControlRow title="mode" note="Plan blocks mutating tool execution.">
            <div className="autonomy-segment">
              {(["plan", "build"] as const).map((mode) => (
                <button key={mode} className="autonomy-toggle" data-active={controls.mode === mode} disabled={saving} onClick={() => save({ mode })}>
                  {mode}
                </button>
              ))}
            </div>
          </ControlRow>
          <ControlRow title="dry run" note="Mutating tools return a blocked result instead of changing state.">
            <div className="autonomy-control-widget">
              <button className="autonomy-toggle" data-active={controls.dry_run} disabled={saving} onClick={() => save({ dry_run: !controls.dry_run })}>
                {controls.dry_run ? "on" : "off"}
              </button>
            </div>
          </ControlRow>
          <ControlRow title="free-first" note="Prefer configured free models before paid providers.">
            <div className="autonomy-control-widget">
              <button className="autonomy-toggle" data-active={controls.prefer_free_models} disabled={saving} onClick={() => save({ prefer_free_models: !controls.prefer_free_models })}>
                {controls.prefer_free_models ? "on" : "off"}
              </button>
            </div>
          </ControlRow>
          <ControlRow title="max steps" note="Hard cap per user turn before the agent stops.">
            <div className="autonomy-control-widget">
              <input
                type="number"
                min={1}
                max={50}
                value={draft.max_steps}
                disabled={saving}
                onChange={(e) => setDraft({ ...draft, max_steps: Number(e.target.value) })}
                onBlur={() => save({ max_steps: draft.max_steps })}
                className="bg-transparent text-right px-3 py-1"
                style={{ border: "1px solid var(--color-border)", color: "var(--color-accent)", height: 34 }}
              />
            </div>
          </ControlRow>
          <div className="grid gap-4 autonomy-pane-pad" style={{ borderTop: "1px solid var(--color-border)" }}>
            <ToolListEditor label="allowed tools" value={draft.allowed_tools} placeholder="empty = all registered tools allowed" onChange={(allowed_tools) => setDraft({ ...draft, allowed_tools })} />
            <ToolListEditor label="blocked tools" value={draft.blocked_tools} placeholder="one tool per line" onChange={(blocked_tools) => setDraft({ ...draft, blocked_tools })} />
            <button
              onClick={saveLists}
              disabled={saving}
              className="justify-self-start text-[10px] uppercase tracking-[0.14em]"
              style={{ color: "var(--color-accent)", background: "transparent", border: "1px solid var(--color-border)", padding: "8px 12px" }}
            >
              [{saving ? "saving" : "save lists"}]
            </button>
          </div>
        </div>

        <div className="autonomy-card">
          <div className="autonomy-card-head">
            <div>
              <div className="hm-rail-label">recent turns</div>
              <div className="text-[11px] mt-1" style={{ color: "var(--color-text-muted)" }}>
                model calls · tools · guards · estimated cost
              </div>
            </div>
            <div className="text-right shrink-0">
              <button
                type="button"
                onClick={refreshReplay}
                disabled={refreshing}
                className="text-[10px] uppercase tracking-[0.14em]"
                style={{ color: "var(--color-accent)", background: "transparent", border: "none", opacity: refreshing ? 0.55 : 1 }}
              >
                [{refreshing ? "loading" : "refresh"}]
              </button>
              <div className="text-[11px] mt-2" style={{ color: "var(--color-text-faint)" }}>{replay.length} loaded</div>
            </div>
          </div>
          {replay.length === 0 ? (
            <div className="p-4 text-[12px]" style={{ color: "var(--color-text-muted)" }}>no replayable turns yet</div>
          ) : (
            replay.map((turn) => (
              <ReplayRow key={turn.id} turn={turn} open={openTurn === turn.id} onToggle={() => setOpenTurn(openTurn === turn.id ? null : turn.id)} />
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function ControlRow({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <div className="autonomy-control-row">
      <div>
        <div className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text)" }}>{title}</div>
        <div className="text-[11px] mt-1" style={{ color: "var(--color-text-muted)" }}>{note}</div>
      </div>
      {children}
    </div>
  );
}

function ToolListEditor({ label, value, placeholder, onChange }: { label: string; value: string[]; placeholder: string; onChange: (next: string[]) => void }) {
  return (
    <label className="grid gap-2">
      <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>{label}</span>
      <textarea className="autonomy-list-input" value={value.join("\n")} placeholder={placeholder} onChange={(e) => onChange(e.target.value.split(/\n|,/).map((x) => x.trim()).filter(Boolean))} />
    </label>
  );
}

function ReplayRow({ turn, open, onToggle }: { turn: AgentReplayTurn; open: boolean; onToggle: () => void }) {
  const blocked = turn.tools.filter((t) => t.status === "blocked").length;
  const toolSummary = turn.tools.length === 0 ? "no tools" : `${turn.tools.length} tools${blocked ? ` · ${blocked} blocked` : ""}`;
  const model = turn.models[turn.models.length - 1]?.model ?? "no model";
  const cost = turn.cost_cents > 0 ? `${turn.cost_cents.toFixed(3)}c` : "$0";

  return (
    <div className="autonomy-replay-row" data-open={open}>
      <button type="button" className="autonomy-replay-summary" onClick={onToggle} aria-expanded={open}>
        <div className="min-w-0">
          <div className="text-[12px] truncate" style={{ color: "var(--color-text)" }}>{turn.user || "(empty user turn)"}</div>
          <div className="mt-1 text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--color-text-muted)" }}>{model} · {toolSummary} · {cost}</div>
        </div>
        <span className="text-[10px]" style={{ color: "var(--color-text-faint)" }}>{open ? "[-]" : "[+]"}</span>
      </button>
      {open && (
        <div className="autonomy-replay-body grid gap-3">
          {turn.tools.map((tool, i) => <ReplayTool key={`${tool.name}-${i}`} tool={tool} />)}
          {turn.guards.length > 0 && (
            <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>
              {turn.guards.map((g) => `${g.event}${g.result ? `: ${g.result}` : ""}`).join(" · ")}
            </div>
          )}
          {turn.assistant && <pre className="autonomy-pre text-[11px]" style={{ color: "var(--color-text-dim)" }}>{turn.assistant}</pre>}
        </div>
      )}
    </div>
  );
}

function ReplayTool({ tool }: { tool: AgentReplayTool }) {
  return (
    <div className="hm-panel-soft p-3">
      <div className="flex justify-between gap-3 text-[10px] uppercase tracking-[0.12em]">
        <span style={{ color: statusColor(tool.status) }}>{tool.status}</span>
        <span style={{ color: "var(--color-text)" }}>{tool.name}</span>
      </div>
      {tool.args && <pre className="autonomy-pre mt-2 text-[11px]" style={{ color: "var(--color-text-muted)" }}>{tool.args}</pre>}
      {tool.result && <pre className="autonomy-pre mt-2 text-[11px]" style={{ color: "var(--color-text-dim)" }}>{tool.result}</pre>}
    </div>
  );
}

function normalizeToolList(values: string[], knownTools: string[]): string[] {
  const known = new Set(knownTools);
  return Array.from(new Set(values.map((v) => v.trim()).filter((v) => v && known.has(v)))).sort();
}

function statusColor(status: AgentReplayTool["status"]): string {
  if (status === "success") return "var(--color-accent)";
  if (status === "blocked") return "var(--color-amber)";
  if (status === "failure") return "var(--color-danger)";
  return "var(--color-text-muted)";
}
