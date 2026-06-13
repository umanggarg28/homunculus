import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Proposal } from "@/lib/types";

/** Proposed skill evolution — the agent's self-authored skills awaiting
 *  the operator's authorization. The most dangerous thing an autonomous
 *  agent does is rewrite itself, so it can't: every new or edited skill
 *  lands here and stays inert until a human approves it. Worn like an
 *  authorization console — but the gate is real, not theatre.
 */
export function SkillProposals() {
  const [items, setItems] = useState<Proposal[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.proposals("pending")
      .then(setItems)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (id: string, kind: "approve" | "reject") => {
    setBusy(id);
    setError(null);
    try {
      if (kind === "approve") await api.proposalApprove(id);
      else await api.proposalReject(id, "rejected by operator");
      setItems((prev) => prev.filter((p) => p.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // Empty queue: stay quiet. This panel only appears when the agent has
  // actually proposed changing itself — which is the unsettling moment.
  if (items.length === 0) return null;

  const accent = "var(--color-warning)";

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── proposed skill evolution · awaiting authorization</span>
        <span style={{ color: accent, textShadow: `0 0 8px ${accent}`, letterSpacing: "0.14em" }}>
          {items.length} PENDING
        </span>
      </div>

      {error && (
        <div className="px-4 py-2 brut-label" style={{ color: "var(--color-danger)", borderBottom: "1px solid var(--color-border)" }}>
          {error}
        </div>
      )}

      {items.map((p) => (
        <div key={p.id} style={{ borderTop: "1px solid var(--color-border)" }}>
          <div className="px-4 py-3 flex items-baseline justify-between gap-3" style={{ fontFamily: "var(--font-mono)" }}>
            <div className="min-w-0">
              <span className="brut-label" style={{ color: accent, letterSpacing: "0.12em" }}>
                {p.kind === "new_skill" ? "NEW" : "EDIT"}
              </span>{" "}
              <span className="brut-label" style={{ color: "var(--color-text)" }}>{p.skill_name}</span>
              <div className="brut-meta mt-1" style={{ color: "var(--color-text-muted)" }}>
                {p.rationale || "(no rationale given)"}
              </div>
              {p.task_spec ? (
                <div className="brut-meta mt-1" style={{ color: "var(--color-text-faint)" }}>
                  + schedules a task on approval
                </div>
              ) : null}
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                className="brut-label"
                disabled={busy === p.id}
                onClick={() => setOpen(open === p.id ? null : p.id)}
                style={btnStyle("var(--color-text-muted)")}
              >
                {open === p.id ? "hide" : "diff"}
              </button>
              <button
                className="brut-label"
                disabled={busy === p.id}
                onClick={() => act(p.id, "reject")}
                style={btnStyle("var(--color-danger)")}
              >
                reject
              </button>
              <button
                className="brut-label"
                disabled={busy === p.id}
                onClick={() => act(p.id, "approve")}
                style={btnStyle("var(--color-accent)")}
              >
                {busy === p.id ? "…" : "approve"}
              </button>
            </div>
          </div>
          {open === p.id && (
            <pre
              className="px-4 py-3"
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                lineHeight: 1.5,
                color: "var(--color-text-muted)",
                background: "color-mix(in srgb, var(--color-bg) 70%, black)",
                borderTop: "1px solid var(--color-border)",
                maxHeight: "340px",
                overflow: "auto",
              }}
            >
              {p.body}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

function btnStyle(color: string): React.CSSProperties {
  return {
    color,
    border: `1px solid ${color}`,
    background: "transparent",
    padding: "3px 10px",
    letterSpacing: "0.1em",
    cursor: "pointer",
  };
}
