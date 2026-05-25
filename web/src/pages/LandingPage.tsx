import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function BlinkCursor() {
  return (
    <span
      style={{
        display: "inline-block",
        width: "8px",
        height: "14px",
        background: "var(--color-accent)",
        verticalAlign: "middle",
        marginLeft: 6,
        animation: "boot-cursor 1s steps(2) infinite",
      }}
    >
      <style>{`@keyframes boot-cursor { 50% { opacity: 0; } }`}</style>
    </span>
  );
}

const BOOT_LINES = [
  "› initialising mcp manager…",
  "› [builtin] up — 15 tools",
  "› [fetch]   up — 1 tool",
  "› watcher armed on homunculus.yaml",
  "› ready.",
];

const ASCII_BRAND = String.raw`
██   ██  ██████  ███    ███ ██    ██ ███    ██  ██████ ██    ██ ██      ██    ██ ███████
██   ██ ██    ██ ████  ████ ██    ██ ████   ██ ██      ██    ██ ██      ██    ██ ██
███████ ██    ██ ██ ████ ██ ██    ██ ██ ██  ██ ██      ██    ██ ██      ██    ██ ███████
██   ██ ██    ██ ██  ██  ██ ██    ██ ██  ██ ██ ██      ██    ██ ██      ██    ██      ██
██   ██  ██████  ██      ██  ██████  ██   ████  ██████  ██████  ███████  ██████  ███████
`.trim();

const TAGLINES = [
  "i tick in milliseconds. i forget on purpose. i remember anything you ask.",
  "i write in markdown, i tick on a schedule, i call tools you can audit.",
  "i never sleep. i wake on a tick or when you talk. you'll see every move.",
  "small process, persistent memory. ask me to remember; i won't drop it.",
  "i act, i log, i pause. nothing here is hidden — read /traces to be sure.",
];

interface Action {
  path: string;
  label: string;
  hint: string;
}

const ACTIONS: Action[] = [
  { path: "/chat",     label: "start a new chat",      hint: "talk to the agent" },
  { path: "/tasks",    label: "review what's pending", hint: "scheduled & recurring work" },
  { path: "/overview", label: "open the dashboard",    hint: "status · activity · failures" },
  { path: "/memory",   label: "browse memory",         hint: "what the agent remembers" },
];

export function LandingPage() {
  const navigate = useNavigate();
  const [typed, setTyped] = useState<string[]>([]);
  const [phase, setPhase] = useState<"boot" | "ready">("boot");
  const [tagline] = useState(() => TAGLINES[Math.floor(Math.random() * TAGLINES.length)]);
  const [glitch, setGlitch] = useState<{ row: number; col: number; ch: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const out: string[] = [];
    async function run() {
      for (let lineIdx = 0; lineIdx < BOOT_LINES.length; lineIdx++) {
        const target = BOOT_LINES[lineIdx];
        let current = "";
        out.push("");
        for (let c = 0; c < target.length; c++) {
          if (cancelled) return;
          current += target[c];
          out[lineIdx] = current;
          setTyped([...out]);
          const ch = target[c];
          const delay = ch === " " ? 24 : ch === "." || ch === "…" ? 90 : 18 + Math.random() * 16;
          await sleep(delay);
        }
        await sleep(140 + Math.random() * 100);
      }
      if (!cancelled) {
        await sleep(200);
        setPhase("ready");
      }
    }
    run();
    return () => { cancelled = true; };
  }, []);

  // CRT brand-mark glitch
  const ASCII_ROWS = ASCII_BRAND.split("\n");
  useEffect(() => {
    let cancelled = false;
    const glitchChars = ["▒", "░", "▓", "▚", "▞", "▙", "▛", "▜"];
    async function tick() {
      while (!cancelled) {
        const wait = 5000 + Math.random() * 9000;
        await sleep(wait);
        if (cancelled) return;
        const row = Math.floor(Math.random() * ASCII_ROWS.length);
        const r = ASCII_ROWS[row];
        const blockCols = [];
        for (let i = 0; i < r.length; i++) if (r[i] === "█") blockCols.push(i);
        if (blockCols.length === 0) continue;
        const col = blockCols[Math.floor(Math.random() * blockCols.length)];
        const ch = glitchChars[Math.floor(Math.random() * glitchChars.length)];
        setGlitch({ row, col, ch });
        await sleep(160 + Math.random() * 120);
        if (cancelled) return;
        setGlitch(null);
      }
    }
    tick();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const renderBrand = () => {
    if (!glitch) return ASCII_BRAND;
    return ASCII_ROWS
      .map((row, r) => {
        if (r !== glitch.row) return row;
        return row.slice(0, glitch.col) + glitch.ch + row.slice(glitch.col + 1);
      })
      .join("\n");
  };

  return (
    <div
      className="min-h-[calc(100vh-48px)] px-10 pt-10 pb-16"
      style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
    >
      <div className="max-w-[960px] mx-auto">
        <pre
          className="m-0 whitespace-pre overflow-hidden"
          style={{
            color: "var(--color-accent)",
            fontSize: "clamp(5px, 0.86vw, 11px)",
            lineHeight: 1.05,
            fontFamily: "var(--font-mono)",
            textShadow: "0 0 22px var(--color-accent-glow)",
            letterSpacing: "0.04em",
          }}
        >
          {renderBrand()}
        </pre>

        <div className="mt-4 mb-2 brut-body" style={{ color: "var(--color-text)", fontStyle: "italic" }}>
          <span style={{ color: "var(--color-accent)" }}>›</span> {tagline}
        </div>
        <div className="mb-10 brut-meta" style={{ color: "var(--color-text-muted)" }}>
          ── personal agent · runtime 0.1.0 · phosphor build ──
        </div>

        <div className="brut-body min-h-[180px]">
          {typed.map((line, i) => {
            const isLast = i === BOOT_LINES.length - 1 && line === BOOT_LINES[i];
            const isActive = i === typed.length - 1 && phase === "boot";
            return (
              <div
                key={i}
                style={{
                  color: isLast ? "var(--color-accent)" : "var(--color-text-muted)",
                  textShadow: isLast ? "0 0 12px var(--color-accent-glow)" : "none",
                }}
              >
                {line}
                {isActive && <BlinkCursor />}
              </div>
            );
          })}
        </div>

        {phase === "ready" && (
          <div className="mt-10">
            <div className="mb-3 brut-meta" style={{ color: "var(--color-text-muted)" }}>
              ── what would you like to do ──
            </div>
            <div className="flex flex-col">
              {ACTIONS.map((a, i) => (
                <button
                  key={a.path}
                  onClick={() => navigate(a.path)}
                  className="brut-body text-left px-3 py-3 transition-colors cursor-pointer"
                  style={{
                    background: "transparent",
                    color: "var(--color-text-dim)",
                    borderTop: i === 0 ? "1px solid var(--color-border)" : "none",
                    borderBottom: "1px solid var(--color-border)",
                    display: "grid",
                    gridTemplateColumns: "20px 1fr auto",
                    columnGap: 12,
                    alignItems: "center",
                  }}
                  onMouseEnter={(e) => {
                    const el = e.currentTarget as HTMLButtonElement;
                    el.style.background = "var(--color-accent)";
                    el.style.color = "var(--color-bg)";
                  }}
                  onMouseLeave={(e) => {
                    const el = e.currentTarget as HTMLButtonElement;
                    el.style.background = "transparent";
                    el.style.color = "var(--color-text-dim)";
                  }}
                >
                  <span>›</span>
                  <span className="uppercase tracking-[0.04em]">{a.label}</span>
                  <span className="brut-label opacity-60">{a.hint}</span>
                </button>
              ))}
            </div>
            <div className="mt-6 brut-meta" style={{ color: "var(--color-text-faint)" }}>
              tip · pick a row, hit ⌘k to search, or use the sidebar
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
