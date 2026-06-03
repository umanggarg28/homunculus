# Homunculus — UI/UX Audit (Awwwards Track)

**Date:** 2026-06-03
**Branch:** `feat/ui-polish-awwwards`
**Current score:** ~7.0 / 10 — Honorable Mention range
**Target:** 9.0+ — Site of the Month range

---

## Strengths

1. **Distinctive aesthetic concept.** "Brutalist Phosphor CRT" is a cohesive, named system with a complete token set. Most agentic-app UIs default to a Notion-clone or a Linear-clone; this has a point of view. Awwwards weighs this heavily as Creativity.
2. **Token discipline.** The CSS custom-property layer (4 surfaces, 3 border weights, 4 text tints, semantic + secondary lights) is the spine of a serious design system. Type ladder is enforced.
3. **Signature moments exist.** The big glowing `10:58:52` countdown on Tasks, the `018 MEMORIES` digit on Memory, the heartbeat strip on Overview, and the `509 CALLS` on Tools — these are the hero elements that judges remember.
4. **Information density without spreadsheet-feel.** Mono grid + hairline borders + uppercase labels create an operator-console feel, rare in this category.
5. **Tasks page is the template.** Hero number + mini-histogram of last 12 runs + status pills (ARMED/DONE/CANCELLED) + three-tier grouping = reads at a glance.

## Weaknesses

1. **Per-page consistency is uneven.** Overview, Memory, Logs, Tasks, Tools each use a different big-number treatment, different table density, different metadata patterns. Five pages = five aesthetics. Click through and the "system" evaporates.
2. **Chat looks like a Slack-from-2014 dev tool.** Most-used screen, most-screenshotted, currently has zero phosphor character. Same green palette but no CRT framing, no operator-console scaffolding, just messages in a column.
3. **Color is monochrome by default, not by intent.** Indigo, amber, rose, phosphor-white tokens are defined and barely used. Pure-green reads as "missed opportunity" rather than "choice". Need: indigo for user signal, amber for scheduled/warn, rose for blocked, phosphor-white for the single most-important number per page.
4. **No depth, no atmosphere.** A CRT aesthetic without scanlines, phosphor bloom, slight chromatic aberration, or vignette feels like a stylesheet, not a screen. Static green on black = "VT100", not "phosphor".
5. **Typography ladder too thin.** Mono 10–14px + one giant number per page. Missing the "third weight" — something between brut-h1 (20px) and brut-display (40–64px) that pulls focus inside content blocks. Tasks works because the giant number anchors it; pages without a hero number read flat.
6. **Sidebar reads as 1992.** Functional, but huge empty bottom area. Needs ambient telemetry (provider health, latency, last fire), compression, or floating treatment.
7. **No micro-states.** No hover bloom on rows, no skeleton during load, no transition on tab switch. Awwwards Usability grade is "does the surface feel alive". A CRT site that doesn't flicker, hum, or scan is a costume.
8. **Inverted hierarchy on Overview.** The "HOMUNCULUS" wordmark is the largest thing on a status page. The status should be the hero.
9. **Cryptic operator vocabulary unexplained.** "MCL-01 STATE IDLE" with no legend. Either explain it, or commit to it as flavor and lean in (tooltips, glossary panel).

## Awwwards rubric (subjective)

| Criterion  | Score | Notes |
|------------|------:|-------|
| Design     | 7.0   | Strong concept, weak finishing. Tokens great; per-page execution drifts. |
| Usability  | 6.5   | Sidebar nav clear, Chat primitive, no loading affordances, no micro-feedback. |
| Creativity | 8.5   | CRT phosphor in 2026 vs. prevailing pastel-glass = real differentiator. |
| Content    | 7.0   | Right things surfaced; vocab occasionally cryptic. |
| Mobile     | n/a   | Not audited; likely weakest axis. |

Weighted: **~7.0**.

---

## Ranked Visual Fix Plan (each = one PR)

The order is chosen so each step compounds — atmosphere makes anchors pop; anchors make palette-deployment meaningful; palette makes Chat redesign land.

### 1. Global CRT Atmosphere (cheapest, broadest uplift)
- Persistent scanline overlay (1px, ~4% opacity, repeating-linear-gradient, pointer-events: none)
- Corner vignette (radial gradient at viewport edges)
- Per-second phosphor flicker (opacity 0.985 ↔ 1.0 keyframe, very subtle)
- Faint chromatic aberration on hero numbers (text-shadow split RGB ~0.5px)
- One CSS file, touches every page at once.

### 2. One-Screen, One-Anchor Rule
- Every page gets exactly one hero element (Tasks already has it; emulate).
- Overview's anchor: countdown number (not wordmark). Demote "HOMUNCULUS" to label.
- Traces anchor: a big LIVE/IDLE/ACTING state pill with seconds-since-last-action.
- Chat anchor: the agent's most-recent thought / "now thinking…" / "now calling X".
- Memory anchor: `018` is already correct.
- Logs anchor: `270.9 KB` is already correct.
- Tools anchor: `509 CALLS` is already correct.

### 3. Deploy the Secondary Palette
- Indigo (`#6CE7FF`) — user-initiated signal: user_message events, chat input border, "USER" labels.
- Amber (`#FFB84D`) — scheduled, ARMED, warn, mid-tier idle status.
- Rose (`#FF5EA8`) — cancelled, blocked, destructive previews.
- Phosphor-white (`#E8F6D6`) — reserved for THE single most important number on each page.
- Audit every hardcoded `var(--color-accent)` and consider whether it should be one of the above.

### 4. Sidebar Telemetry
- Compress nav (reduce vertical rhythm).
- Fill empty area with ambient operator telemetry: provider chain status row, last-fire timestamp, tokens-today bar, current model.
- Make it look like a power supply readout, not a wasted column.

### 5. Rebuild Chat in Phosphor Language
- Wrap each turn in a console frame (`╔══ AGENT ══╗` motif but via borders, not ASCII).
- Tool calls = expandable terminal blocks inline (the `BrutalistToolBlock` component already exists — surface it).
- User-tinted indigo, agent-tinted phosphor.
- Top of page = NOW pane (mirrors Overview "thinking…" state).
- Replace plain message bubbles with operator panes.

### 6. Micro-interactions Pass
- Hover bloom on table rows (subtle phosphor glow).
- Skeleton states using scanline shimmer (matches aesthetic).
- Tab-switch crossfade.
- Click-feedback on buttons (1-frame brightness pump).
- Live event arrival animation in Traces (slide-in + glow-out).

### 7. Type Ladder Expansion
- Add `brut-section-hero` between `brut-h1` (20px) and `brut-display` (40–64px). Target ~28px, used to anchor content blocks that aren't hero-numbers.
- Add `brut-numeric-mid` for inline numbers that should pop but aren't the page anchor.

### 8. Mobile Pass
- Pocket-grade Overview: countdown + last action + "open chat" CTA.
- Sidebar → bottom tab bar on narrow viewports.
- Phosphor flicker disabled on mobile (battery).

---

## Visual Inspiration (concrete moves to steal)

- **Pi.ai** — oversized type, breathing space, one sentence per turn. Bring into Chat only.
- **OpenClaw / Cline** — inline tool-call rendering as folded terminal blocks. Convention; emulate.
- **Letta / MemGPT** — semantic clustering of memories rendered as graph links between cards. Memory page becomes screenshot-worthy.
- **teenage.engineering product pages** — industrial labeling, schematic borders, exact typography energy the sidebar reaches for.
- **Linear changelog** — how to do a dense feed without wall-of-text.
- **NASA Eyes / Earth status dashboards** — control-room references for Overview.
- **Vercel analytics** — big numbers + small sparklines coexisting; Tasks page is already in this zone.

---

## Functionality Improvements (after visual pass)

Ranked by user-impact / lowest-effort first:

1. **Telegram unification.** Telegram, Web Chat, autonomous heartbeat write to separate conversation contexts. Make it one stream.
2. **Memory recall in chat.** When agent references a memory, inline chip → side-panel preview. `[[name]]` links are already stored; render them.
3. **"Run now" + stream-in-place on Tasks.** Click ARMED → side panel streams the run. Removes the trip to Traces.
4. **Task templates.** Recurring patterns ("daily summary", "weekly digest"); one-click create.
5. **Memory hygiene UI.** Show stale memories (no read in 30+ days), one-click forget.
6. **Skills as visible programs.** "When this happens → I do this" panel on Tools page. `last_used`, `success_rate`, "try this skill now" runner.
7. **Real mobile.** Pocket-grade Overview becomes daily driver.
8. **Provider observability in sidebar.** `gemini ✓ · kimi 429 · qwen ok` — already tracked, just surface it.
