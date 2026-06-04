import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "@/lib/api";
import { BackLink } from "@/components/ui/BackLink";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { MemoryContent } from "@/components/memory/MemoryContent";
import type { MemoryEntry } from "@/lib/types";

/** Memory entry view + inline edit.
 *
 *  View mode: rendered as a mono pre block (brutalist).
 *  Edit mode: same block becomes a textarea; ⌘S / Ctrl+S to save,
 *  Esc to cancel. The agent writes here, the user can edit — symmetric.
 */
export function MemoryEntryPage() {
  const { filename = "" } = useParams();
  const [original, setOriginal] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!filename) return;
    api.memoryEntry(filename)
      .then((t) => { setOriginal(t); setDraft(t); })
      .catch((e) => setError(String(e)));
  }, [filename]);

  useEffect(() => {
    api.memoryList().then(setEntries).catch(() => {});
  }, []);

  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        save();
      } else if (e.key === "Escape") {
        cancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, draft]);

  const startEdit = () => {
    setEditing(true);
    setTimeout(() => textareaRef.current?.focus(), 10);
  };
  const cancel = () => {
    setEditing(false);
    if (original !== null) setDraft(original);
  };
  const save = async () => {
    if (!draft.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await api.memoryUpdate(filename, draft);
      setOriginal(draft);
      setEditing(false);
      setSavedAt(Date.now());
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const dirty = editing && original !== null && draft !== original;
  const justSaved = savedAt && Date.now() - savedAt < 4000;

  return (
    <PageShell>
      <BackLink to="/memory" label="Memory" />
      <PageHeader
        title={filename}
        subtitle={editing ? (dirty ? "editing · unsaved" : "editing") : justSaved ? "saved" : "view"}
        actions={
          editing ? (
            <ActionGroup>
              <Bracketed onClick={save} disabled={!dirty || saving} color="var(--color-accent)">
                {saving ? "saving…" : "save ⌘s"}
              </Bracketed>
              <Bracketed onClick={cancel} color="var(--color-text-muted)">cancel</Bracketed>
            </ActionGroup>
          ) : original !== null ? (
            <Bracketed onClick={startEdit} color="var(--color-accent)">edit</Bracketed>
          ) : undefined
        }
      />

      {error && (
        <div
          className="mb-4 px-4 py-2 text-[11px] uppercase tracking-[0.14em]"
          style={{
            border: "1px solid var(--color-danger)",
            color: "var(--color-danger)",
            fontFamily: "var(--font-mono)",
          }}
        >
          ● error · {error}
        </div>
      )}

      {original === null && !error ? null : editing ? (
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          className="block w-full bg-transparent resize-y outline-none"
          style={{
            border: `1px solid ${dirty ? "var(--color-accent)" : "var(--color-border)"}`,
            padding: "16px 18px",
            color: "var(--color-text)",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            lineHeight: 1.65,
            minHeight: "60vh",
            caretColor: "var(--color-accent)",
          }}
        />
      ) : (
        <MemoryContent text={original ?? ""} entries={entries} />
      )}
    </PageShell>
  );
}

function ActionGroup({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center gap-2">{children}</div>;
}

function Bracketed({
  onClick, disabled, color, children,
}: {
  onClick: () => void;
  disabled?: boolean;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      data-tone={color === "var(--color-accent)" ? "accent" : undefined}
      className="hm-bracket-link text-[10px] uppercase tracking-[0.14em] px-2 py-1 disabled:opacity-30"
      style={{
        fontFamily: "var(--font-mono)",
      }}
    >
      [{children}]
    </button>
  );
}
