import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";

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
  "› [builtin] up — core tools mounted",
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

interface HomeTelemetry {
  events: number | null;
  toolsUsed: number | null;
  activeTasks: number | null;
  memoryEntries: number | null;
  lastDirective: string | null;
  status: "loading" | "ready" | "partial";
}

export function LandingPage() {
  const navigate = useNavigate();
  const [typed, setTyped] = useState<string[]>([]);
  const [phase, setPhase] = useState<"boot" | "ready">("boot");
  const [tagline] = useState(() => TAGLINES[Math.floor(Math.random() * TAGLINES.length)]);
  const [glitch, setGlitch] = useState<{ row: number; col: number; ch: string } | null>(null);
  const [telemetry, setTelemetry] = useState<HomeTelemetry>({
    events: null,
    toolsUsed: null,
    activeTasks: null,
    memoryEntries: null,
    lastDirective: null,
    status: "loading",
  });
  const [transmissions, setTransmissions] = useState<{ ts: number; text: string }[]>([]);

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

  useEffect(() => {
    let cancelled = false;
    async function loadTelemetry() {
      const [stats, tasks, memories, chat, sent] = await Promise.allSettled([
        api.statsToday(),
        api.tasksList("all"),
        api.memoryList(),
        api.chatHistory(),
        api.notificationsRecent(6),
      ]);
      if (!cancelled && sent.status === "fulfilled") setTransmissions([...sent.value].reverse());
      if (cancelled) return;
      const loaded = [stats, tasks, memories, chat].filter((r) => r.status === "fulfilled").length;
      // "Last directive" = the last thing the USER told the agent. The
      // old source (agent replay, latest turn) was usually a heartbeat
      // tick, so the card showed the harness's internal prompt.
      const lastDirective = chat.status === "fulfilled"
        ? [...chat.value].reverse().find((m) => m.role === "user")?.content?.trim() || null
        : null;
      setTelemetry({
        events: stats.status === "fulfilled" ? stats.value.events : null,
        toolsUsed: stats.status === "fulfilled" ? stats.value.unique_tools : null,
        activeTasks: tasks.status === "fulfilled"
          ? tasks.value.filter((task) => task.status === "active").length
          : null,
        memoryEntries: memories.status === "fulfilled" ? memories.value.length : null,
        lastDirective: lastDirective ? compactDirective(lastDirective) : null,
        status: loaded === 4 ? "ready" : loaded > 0 ? "partial" : "loading",
      });
    }
    loadTelemetry();
    // Poll every 30s so the home stats don't drift behind the API
    // (events/tasks/memory all change in the background).
    const timer = setInterval(loadTelemetry, 30_000);
    return () => { cancelled = true; clearInterval(timer); };
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
      className="landing-page min-h-[calc(100vh-48px)] px-10 pt-12 pb-24"
      style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
    >
      <style>{`
        .landing-page {
          background:
            linear-gradient(180deg, rgba(119,255,61,0.05), transparent 34%),
            linear-gradient(90deg, rgba(108,231,255,0.025), transparent 38%),
            var(--color-bg) !important;
        }
        .landing-actions {
          border: 1px solid var(--color-border);
          background: linear-gradient(180deg, rgba(119,255,61,0.025), transparent), var(--color-surface-1);
          box-shadow: 0 24px 80px rgba(0,0,0,0.26);
        }
        .landing-action-row {
          color: var(--color-text-dim);
          transition: background 150ms ease, color 150ms ease, box-shadow 180ms ease;
        }
        .landing-action-row:hover,
        .landing-action-row:focus-visible {
          background:
            linear-gradient(90deg, rgba(124,254,0,0.10), rgba(124,254,0,0.025) 42%, transparent),
            var(--color-surface-2);
          color: var(--color-text);
          box-shadow: inset 2px 0 0 var(--color-accent);
        }
        .landing-action-caret {
          color: var(--color-accent);
          transition: transform 150ms ease, text-shadow 180ms ease;
        }
        .landing-action-row:hover .landing-action-caret,
        .landing-action-row:focus-visible .landing-action-caret {
          transform: translateX(2px);
          text-shadow: 0 0 8px var(--color-accent-glow);
        }
        .landing-action-hint {
          color: var(--color-text-muted);
          transition: color 150ms ease, opacity 150ms ease;
        }
        .landing-action-row:hover .landing-action-hint,
        .landing-action-row:focus-visible .landing-action-hint {
          color: var(--color-text-dim);
          opacity: 1;
        }
        .landing-desk {
          display: grid;
          grid-template-columns: minmax(0, 1.18fr) minmax(320px, 0.82fr);
          gap: 28px;
          align-items: start;
          margin-top: 64px;
        }
        .landing-panel {
          border: 1px solid var(--color-border);
          background: linear-gradient(180deg, rgba(119,255,61,0.022), transparent), var(--color-surface-1);
          min-width: 0;
          transition: border-color 180ms ease, box-shadow 220ms ease, transform 180ms ease;
        }
        .landing-panel:focus-within,
        .landing-panel:hover {
          border-color: rgba(67, 133, 105, 0.78);
          box-shadow:
            inset 0 1px 0 rgba(215,245,223,0.035),
            0 18px 58px rgba(0,0,0,0.28),
            0 0 24px rgba(124,254,0,0.035);
        }
        .landing-panel-head {
          padding: 14px 16px;
          border-bottom: 1px solid var(--color-border);
        }
        .landing-telemetry-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          border-bottom: 1px solid var(--color-border);
        }
        .landing-status-dot {
          display: inline-block;
          width: 6px;
          height: 6px;
          margin-right: 8px;
          background: currentColor;
          box-shadow: 0 0 8px currentColor;
          vertical-align: middle;
          animation: landing-status-dot 2.8s steps(1, end) infinite;
        }
        @keyframes landing-status-dot {
          0%, 76%, 100% { opacity: 0.62; }
          82% { opacity: 1; }
          88% { opacity: 0.35; }
          94% { opacity: 1; }
        }
        .landing-telemetry-cell {
          display: flex;
          appearance: none;
          width: 100%;
          text-align: left;
          background: transparent;
          min-height: 110px;
          padding: 15px 16px;
          border-left: 1px solid var(--color-border);
          border-top: 1px solid var(--color-border);
          flex-direction: column;
          justify-content: space-between;
          gap: 18px;
          cursor: pointer;
          color: inherit;
          transition: background 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }
        .landing-telemetry-cell:nth-child(odd) {
          border-left: none;
        }
        .landing-telemetry-cell:nth-child(-n + 2) {
          border-top: none;
        }
        .landing-telemetry-cell:hover {
          background: rgba(124,254,0,0.025);
          box-shadow: inset 2px 0 0 rgba(124,254,0,0.7);
        }
        .landing-telemetry-cell:focus-visible {
          outline: 2px solid var(--color-accent);
          outline-offset: -2px;
        }
        .landing-telemetry-value {
          color: var(--color-accent);
          font-size: 34px;
          line-height: 1;
          font-variant-numeric: tabular-nums;
          text-shadow: 0 0 14px var(--color-accent-glow);
        }
        .landing-boot-log {
          min-height: 164px;
          padding: 16px;
          display: flex;
          flex-direction: column;
          justify-content: center;
        }
        @media (max-width: 760px) {
          .landing-page {
            padding-left: 16px;
            padding-right: 16px;
            padding-top: 20px;
          }
          .landing-desk {
            grid-template-columns: 1fr;
          }
          .landing-telemetry-grid {
            grid-template-columns: 1fr;
          }
          .landing-telemetry-cell {
            min-height: 78px;
            border-left: none;
            border-top: 1px solid var(--color-border);
          }
          .landing-telemetry-cell:first-child {
            border-top: none;
          }
          .landing-action-row {
            grid-template-columns: 20px 1fr !important;
            row-gap: 4px;
          }
          .landing-action-hint {
            grid-column: 2;
            justify-self: start;
          }
        }
      `}</style>
      <div className="max-w-[980px] mx-auto">
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

        <div className="landing-desk">
          <div className="landing-panel instrument-panel hm-panel-scan hm-panel-primary">
            <div className="landing-panel-head brut-meta" style={{ color: "var(--color-text-muted)" }}>
              ── boot channel · {phase}
            </div>
            <div className="landing-boot-log brut-body">
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
            <div className="landing-actions flex flex-col">
              {ACTIONS.map((a, i) => (
                <ActionRow key={a.path} action={a} index={i} onPick={() => navigate(a.path)} />
              ))}
            </div>
          </div>

          <div className="landing-panel instrument-panel hm-panel-scan hm-panel-primary">
            <div className="landing-panel-head brut-meta" style={{ color: "var(--color-text-muted)" }}>
              ── live digest · <span style={{ color: telemetry.status === "ready" ? "var(--color-accent)" : "var(--color-amber)" }}><span className="landing-status-dot" />{telemetry.status}</span>
            </div>
            <div className="landing-telemetry-grid">
              <TelemetryCell label="events today" value={fmtMetric(telemetry.events)} hint="activity volume" onPick={() => navigate("/traces")} />
              <TelemetryCell label="tools used" value={fmtMetric(telemetry.toolsUsed)} hint="capability exercised" onPick={() => navigate("/tools")} />
              <TelemetryCell label="active tasks" value={fmtMetric(telemetry.activeTasks)} hint="autonomy armed" onPick={() => navigate("/tasks")} />
              <TelemetryCell label="memories" value={fmtMetric(telemetry.memoryEntries)} hint="persistent context" onPick={() => navigate("/memory")} />
            </div>
            <div className="px-4 py-3" style={{ borderTop: "1px solid var(--color-border)" }}>
              <div className="brut-label mb-2" style={{ color: "var(--color-text-faint)" }}>last directive</div>
              <div
                className="brut-body"
                style={{
                  color: telemetry.lastDirective ? "var(--color-text-dim)" : "var(--color-text-faint)",
                  overflowWrap: "anywhere",
                  wordBreak: "break-word",
                }}
                title={telemetry.lastDirective ?? undefined}
              >
                {telemetry.lastDirective ?? "awaiting user command"}
              </div>
            </div>
          </div>
        </div>

        {transmissions.length > 0 && (
          <div className="landing-panel instrument-panel hm-panel-scan hm-panel-secondary mt-4">
            <div className="landing-panel-head brut-meta" style={{ color: "var(--color-text-muted)" }}>
              ── transmissions · what reached your phone
            </div>
            <div style={{ fontFamily: "var(--font-mono)" }}>
              {transmissions.map((n, i) => (
                <div
                  key={`${n.ts}-${i}`}
                  className="px-4 py-2 flex gap-10 items-baseline"
                  style={{ borderTop: i > 0 ? "1px solid var(--color-border)" : "none" }}
                >
                  <span className="brut-label" style={{ color: "var(--color-text-faint)", flexShrink: 0, width: 96 }}>
                    {new Date(n.ts * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}
                  </span>
                  <span className="brut-body truncate" style={{ color: "var(--color-text-dim)", minWidth: 0 }} title={n.text}>
                    {firstLine(n.text)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function firstLine(text: string): string {
  const line = text.split("\n").find((l) => l.trim()) ?? "";
  return line.length > 140 ? line.slice(0, 140) + "…" : line;
}

function fmtMetric(value: number | null): string {
  if (value === null) return "—";
  return value.toString().padStart(value < 100 ? 2 : 0, "0");
}

function compactDirective(value: string): string {
  const flat = value.replace(/\s+/g, " ").trim();
  if (flat.length <= 96) return flat;
  // Truncate at a word boundary — mid-word cuts ("the current time is 2026-")
  // read like rendering glitches.
  const cut = flat.slice(0, 96);
  return cut.slice(0, Math.max(40, cut.lastIndexOf(" "))) + " …";
}

function TelemetryCell({ label, value, hint, onPick }: { label: string; value: string; hint: string; onPick: () => void }) {
  return (
    <button type="button" className="landing-telemetry-cell" onClick={onPick}>
      <div>
        <div className="brut-label" style={{ color: "var(--color-text-muted)" }}>{label}</div>
        <div className="brut-label mt-1" style={{ color: "var(--color-text-faint)" }}>{hint}</div>
      </div>
      <div className="landing-telemetry-value">{value}</div>
    </button>
  );
}

function ActionRow({ action, index, onPick }: { action: Action; index: number; onPick: () => void }) {
  return (
    <button
      onClick={onPick}
      className="landing-action-row hm-pressable brut-body text-left px-3 py-3 cursor-pointer"
      style={{
        background: "transparent",
        borderTop: index === 0 ? "1px solid var(--color-border)" : "none",
        borderBottom: "1px solid var(--color-border)",
        display: "grid",
        gridTemplateColumns: "20px 1fr auto",
        columnGap: 12,
        alignItems: "center",
      }}
    >
      <span className="landing-action-caret">›</span>
      <span className="uppercase tracking-[0.04em]">{action.label}</span>
      <span className="landing-action-hint brut-label opacity-60">{action.hint}</span>
    </button>
  );
}
