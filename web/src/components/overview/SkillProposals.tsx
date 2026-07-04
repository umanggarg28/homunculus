import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";
import type { Proposal } from "@/lib/types";

/** Fired when a proposal is approved/rejected so other views (the
 *  sidebar OVERVIEW badge) refresh their pending count immediately
 *  instead of waiting for the next 30s poll. Mirrors broadcastPaused. */
export const PROPOSALS_CHANGED_EVENT = "hm:proposals-changed";

/** Proposed evolution — self-authored skills and memory hygiene changes
 *  awaiting operator authorization. The most dangerous thing an autonomous
 *  agent does is rewrite itself or erase context, so it can't: every change
 *  lands here and stays inert until a human approves it.
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

  // Refetch the instant a proposal is resolved on ANY channel — approving
  // from Discord/Telegram emits `proposal_resolved`, so the panel clears
  // without waiting for the 30s poll (and without a manual page refresh).
  const { events } = useEventStream(200);
  const lastResolvedTs = useRef<string>("");
  useEffect(() => {
    const resolved = events.filter((e) => e.event === "proposal_resolved");
    if (resolved.length === 0) return;
    const latest = resolved[resolved.length - 1];
    if (latest.ts && latest.ts !== lastResolvedTs.current) {
      lastResolvedTs.current = latest.ts;
      load();
      window.dispatchEvent(new CustomEvent(PROPOSALS_CHANGED_EVENT));
    }
  }, [events, load]);

  const act = async (id: string, kind: "approve" | "reject") => {
    setBusy(id);
    setError(null);
    try {
      if (kind === "approve") await api.proposalApprove(id);
      else await api.proposalReject(id, "rejected by operator");
      setItems((prev) => prev.filter((p) => p.id !== id));
      window.dispatchEvent(new CustomEvent(PROPOSALS_CHANGED_EVENT));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const approveAll = async (kind: string) => {
    const ids = items.filter((p) => p.kind === kind).map((p) => p.id);
    setBusy("__batch__");
    setError(null);
    try {
      const res = await api.proposalApproveBatch(ids);
      if (res.failed > 0) {
        const firstErr = res.results.find((r) => !r.ok);
        setError(`${res.approved} approved · ${res.failed} failed${firstErr?.error ? ` — ${firstErr.error}` : ""}`);
      }
      load();
      window.dispatchEvent(new CustomEvent(PROPOSALS_CHANGED_EVENT));
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
  // Batch affordance only for a homogeneous stack of the same kind —
  // e.g. the consolidation scan filing five memory deletions. Mixed
  // queues keep per-item review; skill edits are never batched (each
  // diff deserves its own read).
  const kinds = [...new Set(items.map((p) => p.kind))];
  const batchKind = kinds.length === 1 && items.length >= 2 && kinds[0] === "memory_delete"
    ? kinds[0] : null;

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── proposed evolution · awaiting authorization</span>
        <span className="flex items-center gap-3">
          {batchKind && (
            <button
              className="brut-label"
              disabled={busy !== null}
              onClick={() => approveAll(batchKind)}
              style={{
                border: "1px solid var(--color-accent)",
                color: "var(--color-accent)",
                background: "transparent",
                padding: "3px 8px",
                letterSpacing: "0.1em",
                cursor: busy ? "wait" : "pointer",
              }}
            >
              {busy === "__batch__" ? "approving…" : `approve all ${items.length}`}
            </button>
          )}
          <span style={{ color: accent, textShadow: `0 0 8px ${accent}`, letterSpacing: "0.14em" }}>
            {items.length} PENDING
          </span>
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
                {kindLabel(p.kind)}
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
                {open === p.id ? "hide" : p.kind.startsWith("memory_") ? "body" : "diff"}
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

function kindLabel(kind: Proposal["kind"]): string {
  if (kind === "new_skill") return "NEW SKILL";
  if (kind === "skill_edit") return "EDIT SKILL";
  if (kind === "memory_delete") return "DELETE MEMORY";
  return "PROPOSAL";
}
