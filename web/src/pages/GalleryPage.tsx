import { useState } from "react";
import { FigureFlask, FigureWireframe, FigureSigil } from "@/components/lab/AgentFigures";
import { HomunculusRobot, type RobotPalette } from "@/components/robot/HomunculusRobot";
import { useRobotState } from "@/hooks/useRobotState";

/**
 * Design lab — same content rendered in different aesthetic directions
 * so we can pick before committing to the production UI.
 *
 * - Direction selects the overall style (Brutalist / Marginalia / Atrium / Pulse).
 * - Palette selects the colors. Brutalist supports three palettes
 *   (Phosphor, Plasma, Dot-matrix); the others have a single palette.
 *   Adding a palette = adding an entry to `PALETTES`; the renderer is
 *   CSS-var driven so the swap is one object replacement.
 *
 * Modelled on `umang-portfolio/src/app/palette-lab/page.tsx` — same
 * "data-driven palette tokens" pattern.
 */

type Direction = "brutalist" | "marginalia" | "atrium" | "pulse";

type BrutPalette = {
  id: string;
  label: string;
  note: string;
  swatch: string;
  bg: string;
  fg: string;
  fgBright: string;
  dim: string;
  rule: string;
  ruleStrong: string;
  panelBg: string;
  accent: string;
};

const BRUT_PALETTES: BrutPalette[] = [
  {
    id: "phosphor",
    label: "Phosphor",
    note: "#7cfe00 on near-black — classic green CRT. Confident, technical.",
    swatch: "#7cfe00",
    bg: "#050505",
    fg: "#C8E6C9",
    fgBright: "#ffffff",
    dim: "#4d6b4d",
    rule: "#1a2e1a",
    ruleStrong: "#2d4a2d",
    panelBg: "#0a0f0a",
    accent: "#7cfe00",
  },
  {
    id: "plasma",
    label: "Plasma",
    note: "#ff4d2e on near-black — submarine / warning console. Dramatic.",
    swatch: "#ff4d2e",
    bg: "#0a0202",
    fg: "#f0a890",
    fgBright: "#fff5f0",
    dim: "#7a3530",
    rule: "#2e1010",
    ruleStrong: "#4d1818",
    panelBg: "#100404",
    accent: "#ff4d2e",
  },
  {
    id: "paper",
    label: "Dot-matrix",
    note: "Black ink on paper. Inverted brutalist — feels like a receipt printer.",
    swatch: "#1a1a1a",
    bg: "#f5f1e8",
    fg: "#1a1a1a",
    fgBright: "#000000",
    dim: "#8a7a5a",
    rule: "#c8b890",
    ruleStrong: "#8a7a5a",
    panelBg: "#ede7d0",
    accent: "#000000",
  },
];

const DIRECTIONS: { id: Direction; label: string; note: string }[] = [
  { id: "brutalist",  label: "Brutalist",  note: "Phosphor terminal · hard rectangles · JetBrains Mono. Three palettes available." },
  { id: "marginalia", label: "Marginalia", note: "Dark editorial · Newsreader serif body · tool calls as right-gutter margin notes." },
  { id: "atrium",     label: "Atrium",     note: "Light specimen case · tool calls foregrounded as cards · chat demoted to side." },
  { id: "pulse",      label: "Pulse",      note: "Bioluminescent EKG · living waveform anchor · tool calls drop labeled spikes." },
];

export function GalleryPage() {
  const [direction, setDirection] = useState<Direction>("brutalist");
  const [paletteId, setPaletteId] = useState<string>("phosphor");
  const palette = BRUT_PALETTES.find((p) => p.id === paletteId) ?? BRUT_PALETTES[0];
  const directionMeta = DIRECTIONS.find((d) => d.id === direction)!;

  return (
    <div className="max-w-[1360px] mx-auto px-6 pt-8 pb-20">
      <header className="flex items-baseline justify-between gap-6 mb-6">
        <h1 className="m-0 text-[22px] font-medium tracking-wide uppercase" style={{ fontFamily: "JetBrains Mono, monospace" }}>
          DESIGN LAB <span className="text-[var(--color-text-muted)]">/ direction & palette</span>
        </h1>
        <p className="m-0 max-w-[460px] text-[13px] leading-[1.55] text-[var(--color-text-muted)]">
          Same chat exchange rendered four ways. For Brutalist, three palettes are wired up — flip between them to find the one that reads.
        </p>
      </header>

      <div className="flex flex-wrap gap-2 pb-4 mb-4 border-b border-[var(--color-border)]">
        {DIRECTIONS.map((d) => (
          <button
            key={d.id}
            onClick={() => setDirection(d.id)}
            className="px-4 py-2.5 text-[12px] font-medium uppercase tracking-[0.04em] border transition-colors cursor-pointer"
            style={{
              fontFamily: "JetBrains Mono, monospace",
              background: direction === d.id ? "#fafafa" : "transparent",
              color: direction === d.id ? "#0a0a0a" : "var(--color-text-muted)",
              borderColor: direction === d.id ? "#fafafa" : "var(--color-border)",
            }}
          >
            {d.label}
          </button>
        ))}
      </div>

      {direction === "brutalist" && (
        <div className="flex flex-wrap gap-2 mb-4 -mt-1">
          {BRUT_PALETTES.map((p) => (
            <button
              key={p.id}
              onClick={() => setPaletteId(p.id)}
              className="flex items-center gap-2 px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] border transition-colors cursor-pointer"
              style={{
                fontFamily: "JetBrains Mono, monospace",
                background: paletteId === p.id ? "#1a1a1a" : "transparent",
                color: paletteId === p.id ? "#fafafa" : "var(--color-text-muted)",
                borderColor: paletteId === p.id ? "#525252" : "var(--color-border)",
              }}
            >
              <span className="inline-block w-2 h-2" style={{ background: p.swatch, outline: p.id === "paper" ? "1px solid #c8b890" : "none" }} />
              {p.label}
            </button>
          ))}
        </div>
      )}

      <div className="border border-[var(--color-border)] overflow-hidden bg-black">
        <div
          className="flex items-center justify-between gap-4 px-4 py-3 border-b text-[11px] uppercase tracking-[0.06em]"
          style={{
            fontFamily: "JetBrains Mono, monospace",
            background: "linear-gradient(180deg, #0f0f0f, #0a0a0a)",
            color: "#737373",
            borderColor: "#262626",
          }}
        >
          <span><Dot /> {directionMeta.label}{direction === "brutalist" ? ` · ${palette.label}` : ""}</span>
          <span className="normal-case tracking-normal text-[#525252] font-sans">{directionMeta.note}{direction === "brutalist" ? ` — ${palette.note}` : ""}</span>
        </div>

        {direction === "brutalist"  && <BrutalistMock palette={palette} />}
        {direction === "marginalia" && <MarginaliaMock />}
        {direction === "atrium"     && <AtriumMock />}
        {direction === "pulse"      && <PulseMock />}
      </div>

      <p className="mt-6 text-[12px] text-[var(--color-text-muted)] max-w-[640px] leading-[1.6]">
        To add a palette: add an entry to <code className="text-[var(--color-text)]">BRUT_PALETTES</code> in <code className="text-[var(--color-text)]">GalleryPage.tsx</code>. The mock reads palette tokens via inline CSS variables — no other change required.
      </p>

      {/* ── AGENT FIGURE CANDIDATES ───────────────────────────── */}
      <header className="flex items-baseline justify-between gap-6 mt-16 mb-2">
        <h2
          className="m-0 text-[18px] font-medium tracking-wide uppercase"
          style={{ fontFamily: "JetBrains Mono, monospace" }}
        >
          AGENT FIGURE <span className="text-[var(--color-text-muted)]">/ candidates</span>
        </h2>
        <p className="m-0 max-w-[460px] text-[12.5px] leading-[1.55] text-[var(--color-text-muted)]">
          The "mysterious figure" — a recurring character across the app (Landing hero, sidebar avatar, chat speaker). Each renders live so you see its animation, not a screenshot.
        </p>
      </header>

      <div
        className="grid gap-4 mt-6"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}
      >
        <FigureCard
          letter="A"
          label="Liquid Flask"
          note="Glass vessel, layered liquid with surface ripple, suspended figure that breathes, bubbles rising procedurally. The literal homunculus."
        >
          <FigureFlask />
        </FigureCard>
        <FigureCard
          letter="B"
          label="Wireframe Figure"
          note="Vitruvian-style line-drawn humanoid in a breathing circle. Anatomical nodes light up like neural firings. Cerebral, technical, premium."
        >
          <FigureWireframe />
        </FigureCard>
        <FigureCard
          letter="C"
          label="Generative Sigil"
          note="Multi-layer alchemical sigil — outer tick ring (slow), middle glyph (counter-rotating), pulsing core. Symbol, not figure."
        >
          <FigureSigil />
        </FigureCard>
      </div>

      <p className="mt-6 text-[12px] text-[var(--color-text-muted)] max-w-[640px] leading-[1.6]">
        These are properly built — SVG with gradients, multi-layer animation, atmospheric glow.
        Pick one and I wire it into Landing hero + sidebar + chat.
      </p>

      {/* ── ROBOT PALETTE ─────────────────────────────────────── */}
      <RobotPaletteLab />
    </div>
  );
}

const PALETTES: { id: RobotPalette; label: string; note: string }[] = [
  { id: "phosphor", label: "Phosphor",  note: "matches app accent — robot blends with the world" },
  { id: "cream",    label: "Cream",     note: "warm bone tone — reads as a character in the green world" },
  { id: "amber",    label: "Amber",     note: "vintage CRT amber — classic companion to phosphor green" },
  { id: "cyan",     label: "Cyan",      note: "cool counterpoint — robot feels chrome / metallic" },
  { id: "white",    label: "White",     note: "near-pure white — robot reads as spotlit / glowing" },
];

function RobotPaletteLab() {
  const state = useRobotState();
  return (
    <>
      <header className="flex items-baseline justify-between gap-6 mt-16 mb-2">
        <h2
          className="m-0 text-[18px] font-medium tracking-wide uppercase"
          style={{ fontFamily: "JetBrains Mono, monospace" }}
        >
          ROBOT PALETTE <span className="text-[var(--color-text-muted)]">/ pick a color</span>
        </h2>
        <p className="m-0 max-w-[460px] text-[12.5px] leading-[1.55] text-[var(--color-text-muted)]">
          Same robot, five palettes — running live so you see the state poses, cursor tracking, and antenna glow under each color.
        </p>
      </header>
      <div
        className="grid gap-4 mt-6"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}
      >
        {PALETTES.map((p) => (
          <div
            key={p.id}
            className="flex flex-col"
            style={{
              border: "1px solid var(--color-border)",
              background: "var(--color-bg)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <div
              className="px-4 py-3 flex items-baseline justify-between gap-3"
              style={{
                borderBottom: "1px solid var(--color-border)",
                color: "var(--color-text-muted)",
                fontSize: 10,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
              }}
            >
              <span style={{ color: "var(--color-text)" }}>{p.label}</span>
              <span style={{ color: "var(--color-text-faint)" }}>{state}</span>
            </div>
            <div style={{ height: 240, position: "relative" }}>
              <HomunculusRobot
                state={state}
                palette={p.id}
                detail="mid"
                style={{ width: "100%", height: "100%", display: "block" }}
              />
            </div>
            <div
              className="px-4 py-3"
              style={{
                borderTop: "1px solid var(--color-border)",
                color: "var(--color-text-muted)",
                fontSize: 11.5,
                lineHeight: 1.55,
              }}
            >
              {p.note}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-6 text-[12px] text-[var(--color-text-muted)] max-w-[640px] leading-[1.6]">
        Tell me which one and I update <code className="text-[var(--color-text)]">FloatingRobot.tsx</code> to ship it across the app.
      </p>
    </>
  );
}

function FigureCard({
  letter,
  label,
  note,
  children,
}: {
  letter: string;
  label: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-col"
      style={{
        border: "1px solid var(--color-border)",
        background: "var(--color-bg)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <div
        className="px-4 py-3 flex items-baseline gap-3"
        style={{
          borderBottom: "1px solid var(--color-border)",
          color: "var(--color-text-muted)",
          fontSize: 10,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
        }}
      >
        <span style={{ color: "var(--color-accent)" }}>{letter}</span>
        <span style={{ color: "var(--color-text)" }}>{label}</span>
      </div>
      <div
        className="flex items-center justify-center"
        style={{ minHeight: 240, padding: 24 }}
      >
        {children}
      </div>
      <div
        className="px-4 py-3"
        style={{
          borderTop: "1px solid var(--color-border)",
          color: "var(--color-text-muted)",
          fontSize: 11.5,
          lineHeight: 1.55,
        }}
      >
        {note}
      </div>
    </div>
  );
}

function Dot() {
  return <span className="inline-block w-2 h-2 rounded-full mr-2 align-middle" style={{ background: "#5eead4", boxShadow: "0 0 12px #5eead4" }} />;
}

// ── BRUTALIST ─────────────────────────────────────────────────────

function BrutalistMock({ palette: p }: { palette: BrutPalette }) {
  // Every color comes through CSS variables on the wrapper. Swapping the
  // palette = swapping this style object. Components beneath are
  // palette-agnostic.
  const style = {
    "--bg": p.bg,
    "--fg": p.fg,
    "--fg-bright": p.fgBright,
    "--dim": p.dim,
    "--rule": p.rule,
    "--rule-strong": p.ruleStrong,
    "--panel-bg": p.panelBg,
    "--accent": p.accent,
    background: "var(--bg)",
    color: "var(--fg)",
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 13,
    lineHeight: 1.55,
    padding: "40px 48px 56px",
    minHeight: 680,
  } as React.CSSProperties;

  const isPaper = p.id === "paper";

  return (
    <div style={style}>
      <div className="flex justify-between text-[11px] uppercase pb-2 mb-7" style={{ color: "var(--dim)", borderBottom: "1px solid var(--rule)", letterSpacing: "0.18em" }}>
        <span>HOMUNCULUS · v0.1 · MCP/16 tools online</span>
        <span style={{ color: "var(--accent)" }}>●  ALIVE  ·  18:42:11 IST</span>
      </div>

      <pre className="m-0 whitespace-pre text-[12px] leading-[1.2]" style={{ color: "var(--rule-strong)", fontFamily: "JetBrains Mono, monospace" }}>{`┌─────────────────────────────────────── SESSION 0c91f3 ───────────────────────────────────────┐`}</pre>

      <div className="h-[18px]" />

      <div>
        <span style={{ color: "var(--accent)" }}>user@homunculus:~$</span>{" "}
        <span style={{ color: "var(--fg-bright)" }}>check what people are saying about MCP this week. skip announcements.</span>
      </div>
      <div className="h-3" />
      <div className="my-2" style={{ color: "var(--fg)" }}>› Triangulating across the changelog, SDK release, and your own skill memory…</div>

      <ToolBlock palette={p} rows={[
        { key: "CALL", val: "web_search", status: "5 HITS" },
        { key: "QUERY", val: `"MCP protocol changelog 2026"`, status: "1.8s" },
      ]} isPaper={isPaper} />
      <ToolBlock palette={p} rows={[
        { key: "CALL", val: "fetch__fetch", status: "200 OK" },
        { key: "URL", val: "github.com/modelcontextprotocol/specification/blob/main/CHANGELOG.md", status: "4.3kb" },
      ]} isPaper={isPaper} />
      <ToolBlock palette={p} rows={[
        { key: "CALL", val: "read_file", status: "OK" },
        { key: "PATH", val: "memory/skill_mcp.md", status: "2.1kb" },
      ]} isPaper={isPaper} />

      <div className="h-2" />
      <div className="my-2">› Three things worth your time:</div>
      <div className="my-2">  <b style={{ color: "var(--accent)" }}>1.</b> readOnlyHint is now first-class on FastMCP annotations — clients can</div>
      <div className="my-2">     enforce plan-mode policy without per-tool allow-lists.</div>
      <div className="my-2">  <b style={{ color: "var(--accent)" }}>2.</b> stdio+subprocess is the canonical local transport. SSE is deprecated</div>
      <div className="my-2">     for local use.</div>
      <div className="my-2">  <b style={{ color: "var(--accent)" }}>3.</b> Servers can now expose <b style={{ color: "var(--accent)" }}>resources</b> alongside tools. Implications for</div>
      <div className="my-2">     our memory layer worth thinking about.</div>

      <div className="h-6" />
      <pre className="m-0 whitespace-pre text-[12px] leading-[1.2]" style={{ color: "var(--rule-strong)", fontFamily: "JetBrains Mono, monospace" }}>{`└──────────────────────────────────────────────────────────────────────────────────────────────┘`}</pre>

      <div className="h-5" />
      <div>
        <span style={{ color: "var(--accent)" }}>user@homunculus:~$</span>{" "}
        <BlinkCursor color={p.accent} />
      </div>
    </div>
  );
}

function ToolBlock({
  palette: p,
  rows,
  isPaper,
}: {
  palette: BrutPalette;
  rows: { key: string; val: string; status: string }[];
  isPaper: boolean;
}) {
  return (
    <div className="my-4 px-4 py-3.5" style={{ border: "1px solid var(--rule)", background: "var(--panel-bg)" }}>
      {rows.map((r, i) => (
        <div key={i} className="grid items-start gap-4" style={{ gridTemplateColumns: "80px 1fr 80px" }}>
          <div
            className="text-[10px] uppercase tracking-[0.16em] pt-0.5"
            style={isPaper
              ? { background: "#000", color: p.bg, padding: "2px 6px", justifySelf: "start", borderRadius: 0 }
              : { color: "var(--accent)" }}
          >
            {r.key}
          </div>
          <div className="break-all" style={{ color: "var(--fg)" }}>{r.val}</div>
          <div className="text-[10px] text-right uppercase tracking-[0.12em] pt-0.5" style={{ color: "var(--accent)", fontWeight: isPaper ? 700 : 400 }}>{r.status}</div>
        </div>
      ))}
    </div>
  );
}

function BlinkCursor({ color }: { color: string }) {
  return <span className="inline-block w-[8px] h-[14px] align-middle ml-1 animate-pulse" style={{ background: color }} />;
}

// ── MARGINALIA ────────────────────────────────────────────────────

function MarginaliaMock() {
  return (
    <div style={{ background: "#0F0E0C", color: "#E8E2D6", fontFamily: "Newsreader, Georgia, serif", padding: "56px 64px 80px", minHeight: 680 }}>
      <div className="grid items-end pb-7 mb-10" style={{ gridTemplateColumns: "1fr auto", gap: 24, borderBottom: "1px solid #2A271F" }}>
        <h2 className="m-0" style={{ fontFamily: "Fraunces, serif", fontWeight: 300, fontSize: 56, lineHeight: 0.95, letterSpacing: "0" }}>
          The agent <em style={{ color: "#D4A574", fontWeight: 300 }}>at work</em>.
        </h2>
        <div style={{ font: "400 12px/1 'JetBrains Mono', monospace", color: "#6B6354", letterSpacing: "0.18em", textTransform: "uppercase" }}>Session 0c91f3 · Sat 21 May, 18:42 IST</div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 280px", gap: 64 }}>
        <div style={{ fontSize: 19, lineHeight: 1.65 }}>
          <div className="mb-8 pl-4.5 italic" style={{ color: "#B8AE99", fontSize: 16, borderLeft: "2px solid #D4A574", paddingLeft: 18 }}>
            Check what people are saying about MCP this week and tell me what's actually changed — skip the announcements.
          </div>
          <p style={{ margin: "0 0 20px" }}>
            <span style={{ fontFamily: "Fraunces, serif", fontWeight: 300, fontSize: 72, lineHeight: 0.85, float: "left", padding: "6px 12px 0 0", marginTop: 4, color: "#D4A574" }}>T</span>
            hree things worth your time, separated from the announcement noise. First, <b style={{ color: "#fff", fontWeight: 500 }}>Anthropic's Python SDK 1.16</b> changed how FastMCP servers declare tool annotations — the <em>readOnlyHint</em> field is now first-class, which matters because clients can finally enforce plan-mode policies without per-tool allow-lists.
          </p>
          <p style={{ margin: "0 0 20px" }}>Second, the community settled on <em>stdio + subprocess</em> as the default transport. The earlier SSE-based pattern is officially deprecated for local use.</p>
          <p style={{ margin: "0 0 20px" }}>Third — and this is the one most people missed — the spec now allows servers to expose <b style={{ color: "#fff", fontWeight: 500 }}>resources alongside tools</b>, which means an MCP server can offer documents the LLM reads passively. Worth thinking about for our memory layer.</p>
        </div>
        <aside style={{ borderLeft: "1px solid #2A271F", paddingLeft: 24, fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#8A8270", lineHeight: 1.7 }}>
          <h4 style={{ font: "400 10px/1 'JetBrains Mono', monospace", color: "#6B6354", letterSpacing: "0.22em", textTransform: "uppercase", margin: "0 0 18px" }}>Margin notes</h4>
          <MarginCall tool="web_search" arg='query: "MCP protocol changelog 2026"' result="5 results · 1.8s" />
          <MarginCall tool="fetch__fetch" arg="url: github.com/.../CHANGELOG" result="returned 4.3kb" />
          <MarginCall tool="read_file" arg="path: memory/skill_mcp.md" result="cross-checked priors" last />
        </aside>
      </div>
    </div>
  );
}

function MarginCall({ tool, arg, result, last }: { tool: string; arg: string; result: string; last?: boolean }) {
  return (
    <div className="mb-4 pb-4" style={{ borderBottom: last ? "none" : "1px dashed #2A271F" }}>
      <div style={{ color: "#D4A574", fontWeight: 500, fontSize: 12, letterSpacing: "0.04em" }}>{tool}</div>
      <div style={{ color: "#B8AE99" }}>{arg}</div>
      <div style={{ color: "#6B6354", fontStyle: "italic", fontFamily: "Newsreader, serif" }}>{result}</div>
    </div>
  );
}

// ── ATRIUM ────────────────────────────────────────────────────────

function AtriumMock() {
  return (
    <div className="grid" style={{ background: "#FAFAF7", color: "#1A1A1A", fontFamily: "'Inter Tight', system-ui, sans-serif", padding: "48px 56px 64px", gridTemplateColumns: "1fr 360px", gap: 56, minHeight: 680 }}>
      <div>
        <div className="mb-6" style={{ font: "500 11px/1 'Inter Tight'", color: "#737373", letterSpacing: "0.16em", textTransform: "uppercase" }}>Working · 3 tool calls so far</div>
        <Specimen name="web_search" args={`query: "MCP protocol changelog 2026"`} status="5 hits" running={false}/>
        <Specimen name="fetch__fetch" args="url: github.com/modelcontextprotocol/specification/blob/main/CHANGELOG.md" status="4.3kb" running={false}/>
        <Specimen name="read_file" args="path: memory/skill_mcp.md · cross-referencing priors" status="running" running={true}/>
      </div>
      <div style={{ borderLeft: "1px solid #E5E5E2", paddingLeft: 32 }}>
        <div className="mb-6">
          <div style={{ font: "500 10px/1 'Inter Tight'", letterSpacing: "0.18em", textTransform: "uppercase", color: "#a3a3a3", marginBottom: 6 }}>You · 18:42</div>
          <div style={{ font: "400 15px/1.55 'Inter Tight'", color: "#525252", fontStyle: "italic" }}>Check what people are saying about MCP this week and tell me what's actually changed — skip the announcements.</div>
        </div>
        <div className="mb-6">
          <div style={{ font: "500 10px/1 'Inter Tight'", letterSpacing: "0.18em", textTransform: "uppercase", color: "#a3a3a3", marginBottom: 6 }}>Agent · responding</div>
          <div style={{ font: "400 15px/1.55 'Inter Tight'", color: "#262626" }}>Three things worth your time, separated from announcement noise…</div>
        </div>
        <div className="px-4 py-3.5" style={{ border: "1px solid #E5E5E2", background: "#fff", borderRadius: 10, font: "400 14px/1.4 'Inter Tight'", color: "#a3a3a3" }}>Reply…</div>
      </div>
    </div>
  );
}

function Specimen({ name, args, status, running }: { name: string; args: string; status: string; running: boolean }) {
  return (
    <div className="mb-4 px-6 py-5 grid items-center gap-4.5"
      style={{ background: "#fff", border: "1px solid #E5E5E2", borderRadius: 12, gridTemplateColumns: "44px 1fr auto", boxShadow: "0 1px 0 rgba(0,0,0,0.03), 0 12px 28px -16px rgba(0,0,0,0.08)" }}>
      <div style={{ width: 44, height: 44, borderRadius: 10, background: running ? "#FFFBEB" : "#F0EFEA", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="#1A1A1A" strokeWidth="1.4">
          <circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>
        </svg>
      </div>
      <div>
        <div style={{ font: "600 16px/1.2 'Inter Tight'", letterSpacing: "0" }}>{name}</div>
        <div style={{ font: "400 13px/1.5 'JetBrains Mono', monospace", color: "#737373", marginTop: 4 }}>{args}</div>
      </div>
      <div style={{
        font: "500 11px/1 'Inter Tight'", letterSpacing: "0.08em", textTransform: "uppercase",
        color: running ? "#B45309" : "#15803D",
        padding: "6px 10px", background: running ? "#FFFBEB" : "#ECFDF5", borderRadius: 999,
      }}>
        {status}
      </div>
    </div>
  );
}

// ── PULSE ────────────────────────────────────────────────────────

function PulseMock() {
  return (
    <div className="relative flex flex-col" style={{ background: "#060908", color: "#DCFCE7", fontFamily: "'Geist Mono', monospace", padding: "48px 56px 0", minHeight: 680 }}>
      <div className="absolute top-6 right-8 text-right text-[10px] leading-[1.5]" style={{ color: "#4ade80", letterSpacing: "0.08em" }}>
        <span className="block text-[12px] font-medium" style={{ color: "#5eead4" }}>∼ 24bpm</span>
        local · alive · 03:12 uptime
      </div>
      <div className="mb-5" style={{ font: "400 10px/1 'Geist Mono'", color: "#4d7c6f", letterSpacing: "0.22em", textTransform: "uppercase" }}>Session 0c91f3 · turn 4</div>
      <h2 className="m-0 mb-2" style={{ font: "300 40px/1.05 'Instrument Serif', 'Fraunces', serif", fontStyle: "italic", color: "#fff", letterSpacing: "0", maxWidth: 720 }}>
        What's actually changed in MCP this week.
      </h2>
      <div className="mb-9 max-w-[640px]" style={{ font: "400 13px/1.6 'Geist Mono'", color: "#6ee7b7", letterSpacing: "0.01em" }}>
        Three signals after filtering announcements and release-note noise — pulled from the spec changelog, an SDK release, and your skill memory.
      </div>
      <div className="mb-2 max-w-[700px]" style={{ font: "400 15px/1.65 'Inter Tight', sans-serif", color: "#ECFDF5" }}>
        <b style={{ color: "#5eead4" }}>readOnlyHint</b> is now first-class on FastMCP tool annotations — clients can enforce plan-mode without allow-lists. <b style={{ color: "#5eead4" }}>stdio + subprocess</b> is the canonical local transport; SSE is deprecated. <b style={{ color: "#5eead4" }}>Resources</b> alongside tools — servers can expose documents the LLM reads passively.
      </div>
      <div className="mt-auto pt-6 pb-4">
        <div className="flex justify-between mb-2.5" style={{ font: "400 9px/1 'Geist Mono'", color: "#3f6f5f", letterSpacing: "0.18em", textTransform: "uppercase" }}>
          <span>−24m</span><span>−18m</span><span>−12m</span><span>−6m</span><span>now</span>
        </div>
        <div className="relative h-[100px]">
          <svg viewBox="0 0 1000 100" preserveAspectRatio="none" style={{ width: "100%", height: 100, display: "block" }}>
            <defs>
              <linearGradient id="pulse-glow" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#5eead4" stopOpacity=".5"/>
                <stop offset="1" stopColor="#5eead4" stopOpacity="0"/>
              </linearGradient>
            </defs>
            <path d="M0 50 L120 50 L130 50 L140 40 L150 70 L160 20 L170 80 L180 50 L320 50 L330 35 L340 65 L350 30 L360 70 L370 50 L520 50 L530 50 L540 25 L555 75 L570 50 L720 50 L735 45 L750 55 L765 20 L780 80 L795 50 L850 50 L860 48 L870 52 L1000 50"
              fill="none" stroke="#5eead4" strokeWidth="1.6" strokeLinejoin="round" />
            <path d="M0 50 L120 50 L130 50 L140 40 L150 70 L160 20 L170 80 L180 50 L320 50 L330 35 L340 65 L350 30 L360 70 L370 50 L520 50 L530 50 L540 25 L555 75 L570 50 L720 50 L735 45 L750 55 L765 20 L780 80 L795 50 L850 50 L860 48 L870 52 L1000 50 L1000 100 L0 100 Z"
              fill="url(#pulse-glow)" />
          </svg>
          {[
            { left: "16%", label: "web_search" },
            { left: "35%", label: "fetch__fetch" },
            { left: "55%", label: "read_file" },
            { left: "77%", label: "fetch__fetch" },
          ].map((s) => (
            <span key={s.left} className="absolute" style={{ left: s.left, bottom: -16, transform: "translateX(-50%)", font: "500 10px/1 'Geist Mono'", color: "#5eead4", letterSpacing: "0.04em", textTransform: "lowercase" }}>
              <span className="absolute left-1/2 bottom-full w-px h-3" style={{ background: "#5eead4", opacity: 0.5, transform: "translateX(-50%)" }} />
              {s.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
