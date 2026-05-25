import { Link } from "react-router-dom";
import { useState, type MouseEvent } from "react";
import { api } from "@/lib/api";
import type { MemoryEntry } from "@/lib/types";

interface Props {
  entry: MemoryEntry;
  onDeleted: (filename: string) => void;
}

const TYPE_COLOR: Record<string, string> = {
  user:      "var(--color-info)",
  feedback:  "var(--color-amber)",
  project:   "var(--color-text-dim)",
  reference: "var(--color-text-muted)",
  skill:     "var(--color-accent)",
};

/** Brutalist memory row — hairline border, mono everywhere, type as
 *  text tag (no chip), bracketed [delete] on hover. */
export function MemoryCard({ entry, onDeleted }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const typeColor = TYPE_COLOR[entry.type] ?? "var(--color-text-muted)";

  const startDelete = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); setConfirming(true); };
  const cancel = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); setConfirming(false); };
  const confirm = async (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    setBusy(true);
    try { await api.memoryDelete(entry.filename); onDeleted(entry.filename); }
    catch { setBusy(false); setConfirming(false); }
  };

  return (
    <div
      className="group transition-colors"
      style={{
        borderBottom: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--color-surface-2)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
    >
      <Link
        to={`/memory/${entry.filename}`}
        className="block px-4 py-2"
      >
        <div className="grid items-baseline gap-5" style={{ gridTemplateColumns: "auto 1fr auto" }}>
          <span className="brut-label shrink-0" style={{ color: typeColor, minWidth: 64 }}>
            [{entry.type}]
          </span>
          <div className="min-w-0">
            <div className="brut-body truncate" style={{ color: "var(--color-text)" }}>
              {entry.name}
            </div>
            <div className="mt-1 brut-body-sm truncate" style={{ color: "var(--color-text-muted)" }}>
              {entry.description}
            </div>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            {!confirming ? (
              <button
                onClick={startDelete}
                className="text-[10px] uppercase tracking-[0.14em] px-2 py-1 opacity-0 group-hover:opacity-100 transition-opacity"
                style={{
                  background: "transparent",
                  color: "var(--color-text-muted)",
                  border: "1px solid var(--color-border)",
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.color = "var(--color-danger)";
                  el.style.borderColor = "var(--color-danger)";
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.color = "var(--color-text-muted)";
                  el.style.borderColor = "var(--color-border)";
                }}
              >
                [delete]
              </button>
            ) : (
              <>
                <button
                  onClick={confirm}
                  disabled={busy}
                  className="text-[10px] uppercase tracking-[0.14em] px-2 py-1 disabled:opacity-40"
                  style={{
                    background: "var(--color-danger)",
                    color: "var(--color-bg)",
                    border: "1px solid var(--color-danger)",
                  }}
                >
                  [{busy ? "…" : "confirm"}]
                </button>
                <button
                  onClick={cancel}
                  className="text-[10px] uppercase tracking-[0.14em] px-2 py-1"
                  style={{
                    background: "transparent",
                    color: "var(--color-text-muted)",
                    border: "1px solid var(--color-border)",
                  }}
                >
                  [cancel]
                </button>
              </>
            )}
          </div>
        </div>
      </Link>
    </div>
  );
}
