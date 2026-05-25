import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

interface Command {
  id: string;
  label: string;
  hint: string;
  run: () => void;
  keywords?: string;
}

/** Brutalist command palette — ⌘K / Ctrl+K opens. Fuzzy-match prefix,
 *  Enter to run, Esc to close. Replaces clicking through the sidebar
 *  for power users. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(
    () => [
      { id: "home",     label: "go home",         hint: "/",         run: () => navigate("/")        },
      { id: "overview", label: "open overview",   hint: "/overview", run: () => navigate("/overview") },
      { id: "chat",     label: "open chat",       hint: "/chat",     run: () => navigate("/chat")     },
      { id: "tasks",    label: "open tasks",      hint: "/tasks",    run: () => navigate("/tasks")    },
      { id: "memory",   label: "browse memory",   hint: "/memory",   run: () => navigate("/memory")   },
      { id: "skills",   label: "browse skills",   hint: "/skills",   run: () => navigate("/skills")   },
      { id: "traces",   label: "open traces",     hint: "/traces",   run: () => navigate("/traces")   },
      { id: "logs",     label: "open logs",       hint: "/logs",     run: () => navigate("/logs")     },
      { id: "lab",      label: "open design lab", hint: "/lab",      run: () => navigate("/lab")      },
    ],
    [navigate],
  );

  // Global hotkey
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
        setQuery("");
        setHighlight(0);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Focus input on open
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 10);
  }, [open]);

  const filtered = useMemo(() => {
    if (!query.trim()) return commands;
    const q = query.toLowerCase();
    return commands.filter(
      (c) =>
        c.id.includes(q) ||
        c.label.toLowerCase().includes(q) ||
        c.hint.toLowerCase().includes(q) ||
        (c.keywords ?? "").toLowerCase().includes(q),
    );
  }, [commands, query]);

  if (!open) return null;

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(filtered.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const cmd = filtered[highlight];
      if (cmd) {
        cmd.run();
        setOpen(false);
      }
    }
  };

  return (
    <div
      className="fixed inset-0 z-[150] flex items-start justify-center pt-[16vh]"
      style={{
        background: "rgba(5, 5, 5, 0.78)",
        backdropFilter: "blur(2px)",
        fontFamily: "var(--font-mono)",
      }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-[520px] mx-4"
        style={{
          background: "var(--color-bg)",
          border: "1px solid var(--color-accent)",
          boxShadow: "0 0 32px var(--color-accent-glow)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: "var(--color-border)" }}>
          <span style={{ color: "var(--color-accent)" }}>$</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setHighlight(0); }}
            onKeyDown={onInputKey}
            placeholder="type a command…"
            className="flex-1 bg-transparent outline-none border-none"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 14,
              color: "var(--color-text)",
              caretColor: "var(--color-accent)",
            }}
          />
          <span className="text-[10px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-faint)" }}>
            esc to close
          </span>
        </div>
        <div className="max-h-[40vh] overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="px-4 py-4 text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-text-muted)" }}>
              ─ no matches ─
            </div>
          ) : (
            filtered.map((c, i) => (
              <div
                key={c.id}
                onClick={() => { c.run(); setOpen(false); }}
                onMouseEnter={() => setHighlight(i)}
                className="grid items-baseline px-4 py-2 cursor-pointer transition-colors"
                style={{
                  gridTemplateColumns: "20px 1fr auto",
                  columnGap: 12,
                  background: i === highlight ? "var(--color-accent)" : "transparent",
                  color: i === highlight ? "var(--color-bg)" : "var(--color-text-dim)",
                }}
              >
                <span style={{ color: i === highlight ? "var(--color-bg)" : "var(--color-accent)" }}>
                  {i === highlight ? ">" : " "}
                </span>
                <span className="text-[13px] uppercase tracking-[0.04em]">{c.label}</span>
                <span className="text-[10px] uppercase tracking-[0.14em]" style={{ color: i === highlight ? "var(--color-bg)" : "var(--color-text-faint)" }}>
                  {c.hint}
                </span>
              </div>
            ))
          )}
        </div>
        <div
          className="px-4 py-2 text-[10px] uppercase tracking-[0.14em] flex justify-between"
          style={{ borderTop: "1px solid var(--color-border)", color: "var(--color-text-faint)" }}
        >
          <span>↑↓ navigate · ↵ run</span>
          <span>⌘K · ctrl k</span>
        </div>
      </div>
    </div>
  );
}
