import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { Task } from "@/lib/types";
import { parseServerIso } from "@/lib/api";

interface Props { task: Task | null; onClose: () => void; }

/** Brutalist task detail drawer — slides in from the right. Hairline
 *  borders, mono throughout. Last-runs render as terminal-style log
 *  blocks instead of cards. */
export function TaskDetailDrawer({ task, onClose }: Props) {
  useEffect(() => {
    if (!task) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, onClose]);

  if (!task) return null;

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(5,5,5,0.72)", backdropFilter: "blur(1px)" }}
        onClick={onClose}
      />
      <aside
        className="fixed top-0 right-0 bottom-0 z-50 w-[520px] max-w-[92vw] overflow-y-auto"
        style={{
          background: "var(--color-bg)",
          borderLeft: "1px solid var(--color-accent)",
          fontFamily: "var(--font-mono)",
          boxShadow: "-12px 0 32px rgba(0,0,0,0.4)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 pt-5 pb-4" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex items-center gap-3 mb-3 text-[10px] uppercase tracking-[0.16em]">
            <span style={{ color: statusColor(task.status) }}>● {task.status}</span>
            <span style={{ color: "var(--color-border-strong)" }}>──</span>
            <span style={{ color: cadenceColor(task.recurrence) }}>
              [{task.recurrence === "none" ? "one-shot" : task.recurrence}]
            </span>
            {task.notify && (
              <span style={{ color: "var(--color-amber)" }}>[notify]</span>
            )}
            <span className="flex-1" />
            <button
              onClick={onClose}
              className="text-[10px] uppercase tracking-[0.16em] transition-colors"
              style={{ background: "transparent", color: "var(--color-text-muted)", border: "none", cursor: "pointer" }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-muted)"; }}
            >
              [close esc]
            </button>
          </div>
          <h2 className="text-[16px]" style={{ color: "var(--color-text)", margin: 0, lineHeight: 1.3 }}>
            <span style={{ color: "var(--color-accent)" }}>$</span> {task.title}
          </h2>
        </div>

        {/* KV block */}
        <div
          className="px-6 py-5"
          style={{ borderBottom: "1px solid var(--color-border)" }}
        >
          <KV label="id"        value={task.id} />
          <KV label="due"       value={task.due_at ? fmtDateTime(task.due_at) : "—"} />
          <KV label="created"   value={fmtDateTime(task.created_at)} />
          <KV label="updated"   value={fmtDateTime(task.updated_at)} />
        </div>

        {/* Description */}
        {task.description && (
          <div className="px-6 py-5" style={{ borderBottom: "1px solid var(--color-border)" }}>
            <div className="text-[10px] uppercase tracking-[0.16em] mb-3" style={{ color: "var(--color-text-muted)" }}>
              ── description
            </div>
            <div
              className="whitespace-pre-wrap text-[13px]"
              style={{ color: "var(--color-text)", lineHeight: 1.65 }}
            >
              {task.description}
            </div>
          </div>
        )}

        {/* Last runs */}
        <div className="px-6 py-5">
          {(() => {
            const runs = task.last_runs ?? [];
            const ok = runs.filter((r) => r.status === "success").length;
            const errRate = runs.length > 0 ? Math.round(((runs.length - ok) / runs.length) * 100) : null;
            return (
              <div className="flex items-baseline gap-4 mb-3">
                <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>
                  ── last runs · {runs.length.toString().padStart(2, "0")}
                </span>
                {runs.length > 0 && (
                  <>
                    <span className="text-[10px] uppercase tracking-[0.12em]" style={{ color: ok === runs.length ? "var(--color-accent)" : "var(--color-text-faint)" }}>
                      {ok}/{runs.length} ok
                    </span>
                    {errRate !== null && errRate > 0 && (
                      <span className="text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--color-danger)" }}>
                        {errRate}% err
                      </span>
                    )}
                  </>
                )}
              </div>
            );
          })()}
          {!task.last_runs || task.last_runs.length === 0 ? (
            <div className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-faint)" }}>
              ─ never fired ─
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {[...task.last_runs].reverse().map((r, i) => (
                <div
                  key={i}
                  className="px-3 py-2"
                  style={{ border: `1px solid ${r.status === "success" ? "var(--color-border)" : "var(--color-danger)"}` }}
                >
                  <div className="flex items-baseline justify-between mb-1.5 text-[10px] uppercase tracking-[0.12em]">
                    <span style={{ color: r.status === "success" ? "var(--color-accent)" : "var(--color-danger)" }}>
                      ● {r.status}
                    </span>
                    <span style={{ color: "var(--color-text-faint)", fontVariantNumeric: "tabular-nums", display: "flex", gap: 8 }}>
                      {r.duration_s != null && (
                        <span>{r.duration_s}s</span>
                      )}
                      <span>{fmtDateTime(r.ts)}</span>
                    </span>
                  </div>
                  <div
                    className="text-[12px] whitespace-pre-wrap break-words"
                    style={{ color: "var(--color-text-dim)", lineHeight: 1.55 }}
                  >
                    {r.result || "—"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </>,
    document.body,
  );
}

function fmtDateTime(iso: string): string {
  const ms = parseServerIso(iso);
  if (!Number.isFinite(ms)) return iso;
  const d = new Date(ms);
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[80px_1fr] gap-3 py-1.5" style={{ borderBottom: "1px dashed var(--color-border)" }}>
      <div className="text-[10px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </div>
      <div className="text-[12px] truncate" style={{ color: "var(--color-text)", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
    </div>
  );
}

function statusColor(s: string): string {
  if (s === "active") return "var(--color-accent)";
  if (s === "cancelled") return "var(--color-amber)";
  return "var(--color-text-muted)";
}

function cadenceColor(r: string): string {
  if (r === "daily") return "var(--color-accent)";
  if (r === "weekly") return "var(--color-amber)";
  return "var(--color-text-faint)";
}
