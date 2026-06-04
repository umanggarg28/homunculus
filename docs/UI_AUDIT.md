# Homunculus — UI/UX Audit (Awwwards Track)

**Last updated:** 2026-06-04
**Branch:** `feat/ui-polish-awwwards`
**Current score:** ~7.0 / 10 — Honorable Mention range
**Target:** 9.0+ — Site of the Month range
**Working aesthetic:** Calm Brutalist Phosphor CRT (user-rejected: pulsing/blooming/loud motion — must stay restrained)

---

## Per-Page Validation (data correctness)

Audited every page against the API on 2026-06-04. Mostly accurate — three labelling bugs and minor drift.

### `/` Home
| Display | API truth | Status |
|---|---|---|
| 254 events today | `events: 257` | ⚠ stale (~3 events drift; page doesn't auto-refresh) |
| 14 unique tools | `unique_tools: 14` | ✓ |
| 03 tasks fired | `tasks_fired: 3` | ✓ |
| 18 memories | `len(memory)=18` | ✓ |

**Issue (structural):** Home and `/overview` show overlapping content. Both serve as dashboards with different layouts. Pick one as the dashboard; demote the other.

### `/overview`
| Display | API truth | Status |
|---|---|---|
| `31:00` countdown | next_tick 20:32 UTC, now 20:01 → 31 min | ✓ |
| `254 / 14 / 3 / ¢5.1` | matches stats endpoint | ✓ |
| `ARMED 21/24` | usedTools=21 / total skills=24 | ✓ but **opaque label** |
| `1%` | context: 4850/1048576 = 0.46% | ✓ but **ambiguous** (could read as cost %) |
| `ASSISTANT REPLY · It's a scheduled heartbeat tick…` | The text is the **user prompt** sent to the agent at the start of a heartbeat tick. Mislabelled as the assistant's reply. | ✗ **bug** |

### `/tasks`
| Display | API truth | Status |
|---|---|---|
| `07:28:27` countdown | next due 2026-06-04T03:30 = 7h28m away | ✓ |
| 3 active, 2 completed, 1 cancelled | API matches | ✓ |
| LAST 12 FIRES histogram | matches `last_runs` per task | ✓ |

**Issue:** Cancelled task uses identical visual tone to completed (both muted text). Cancelled is a state user opted out of; it deserves rose tint, not just dimmed.

### `/chat`
- Markdown rendering works (table, code fence, ordered list)
- **Bug (just fixed):** Ordered-list counter `01.` was baseline-misaligned (11px vs 13px body) — fix landed via `font-size: inherit`
- **Bug (just fixed):** Stray `·` rendered for empty nested `<li>` (agent occasionally emits trailing blank item)
- **Critical UX gap:** Chat looks like a 2014 dev tool — no phosphor framing, plain message column. Most-used surface, least styled.

### `/memory`
| Display | API truth | Status |
|---|---|---|
| 018 entries | `len=18` | ✓ |
| USER 03, FEEDBACK 08, PROJECTS 04, SKILLS 03 | matches `by type` | ✓ |
| Newest = "DELIVERED LEETCODE PROBLEM" | matches most-recent mtime | ✓ |

**Issue:** Each category section has identical visual weight regardless of count. USER (3) reads as visually equal to FEEDBACK (8). No "size" affordance.

### `/tools`
| Display | API truth | Status |
|---|---|---|
| 507 calls | sum(call_count) = 507 | ✓ |
| Top tools (read_file 130, web_search 79, web_fetch 34, notify 29, recall 28) | matches API | ✓ |

**Issue (structural):** Page is too dense — hero band + autonomy console + tool histogram + tools list, all stacked, no clear primary action. Reads like a config panel, not a dashboard.

### `/traces`
- Filter chips behave inclusively after recent fix (SYS now means "show only sys events")
- SSE initial-tail change ensures real activity loads on first connect (was previously dominated by service_pings)
- **Issue:** Raw log expands full tool payloads inline → page scrolls forever. Need collapse-by-default with click-to-expand.
- **Issue:** Default Gantt window 15M; on idle hours, lanes show 00 even though events exist within 1H. Should auto-widen to first window that contains activity.

### `/logs`
- 270.9 KB total ✓
- 19 days listed, sparkline of bytes/day ✓
- **Cleanest page in the system** — should serve as the editorial-restraint template for the rest.

---

## Bugs Found (priority ordered)

| # | Severity | Bug | Where |
|---|---|---|---|
| 1 | high | `/overview` "ASSISTANT REPLY" mislabels the heartbeat user prompt as the agent's reply | OverviewPage.tsx — MissionControl section |
| 2 | high | `/chat` ordered list counter baseline-misaligned (just fixed; needs build) | index.css `.brut-md ol > li::before` |
| 3 | med  | `/chat` stray `·` from empty nested `<li>` (just fixed; needs build) | index.css `.brut-md li li:empty` |
| 4 | med  | Home stats lag API by N events (no polling) | LandingPage.tsx |
| 5 | med  | Traces raw log expands full tool payloads inline → infinite scroll | FeedRow.tsx |
| 6 | med  | Traces default window 15M too narrow when agent is idle; lanes show 00 | TracesTimeline.tsx |
| 7 | low  | Cancelled tasks visually identical to completed | TaskRow.tsx `getBadge` |
| 8 | low  | `ARMED 21/24` opaque without legend | OverviewPage.tsx `buildReadiness` |
| 9 | low  | `1%` context could read as cost percentage | ContextStatusCell |
| 10 | low | Stray `MCL-01 STATE IDLE` operator vocab unexplained | PageHeader subtitle row |

Already fixed this session:
- ✓ Sidebar `filter` containing-block bug (sidebar grew to document height)
- ✓ ScrollToTop on route change (Chat scroll inherited by next page)
- ✓ SSE initial tail now scans back for real events (was 50 events of pings)
- ✓ SYS filter behaves as an event-type chip (was a toggle that confused with the chips)

---

## Visual Plan to 9/10 — *Calm Brutalist* Edition

User rejected the previous "phosphor bloom + chromatic split + pulse" pass as "loses the calm brutalist character." The path to 9/10 must therefore be **subtractive editorial discipline**, not additive ornament. References to lean on: **Stripe Press, teenage.engineering product pages, Linear's changelog, vercel/v0 marketing, Apple editorial.**

Principles for this pass:
1. **Restraint first** — every motion / color / glow needs justification
2. **Editorial typography** — one hero size, one body, one micro; nothing in between
3. **Whitespace is a design element** — current pages feel cramped, especially Tools
4. **One bold idea per page** — Tasks already nails it (countdown); other pages should follow
5. **Hard borders, no shadows** — already in place; protect against feature creep
6. **Color with intent** — phosphor green for "agent live"; everything else neutral

### Phase 1 — Editorial Restraint Pass (subtractive)
Touch every page; remove visual noise without adding any.
- Strip decorative dashes (`── label`) where labels are obvious
- Remove redundant borders inside cards (collapse double-bordered sections)
- Reduce sidebar nav row padding (current vertical rhythm too generous)
- Replace operator-jargon (`MCL-01`, `ARMED 21/24`) with plain English on hover, glyph on rest
- Audit every `text-shadow` / `box-shadow` — keep only on the page-anchor element

### Phase 2 — Hero Anchor Discipline
Currently: Tasks, Memory, Logs, Tools all have hero numbers via `HeroBand`. Overview has the countdown but it's smaller than the wordmark. Home is unanchored.

Fix:
- **Home:** Demote the giant `HOMUNCULUS` wordmark (already in sidebar). The anchor should be the heartbeat status / countdown.
- **Overview:** Already has the countdown — promote it; demote the redundant heartbeat strip below.
- **Chat:** New anchor = NOW pane at the top — agent's current state in oversized type ("thinking", "calling notify", "idle 14m"). Echoes the Pi.ai oversized-type calm.

### Phase 3 — Page Density Rebalance
- **Tools:** Split into two views via tab — `console` (autonomy + recent turns) and `tools` (histogram + list). Each becomes editorial-clean.
- **Traces:** Raw log collapses tool payloads by default; click-to-expand. Cut visible page height by 4×.
- **Overview:** Demote 4-column readiness row to a 2-column compact block — `ACTIVITY · AUTONOMY` and `ATTENTION · CAPABILITY`.

### Phase 4 — Chat as Editorial
This is the single highest-impact visual change. Currently a plain markdown column.
- Wrap each turn in operator-pane framing (borders that don't compete with content)
- Tool calls become folded terminal blocks (component `BrutalistToolBlock` exists; surface it)
- User indigo / agent phosphor as TEXT color only — no background fills (rejected last time)
- Reduce content width to ~720px for line-length readability
- Add NOW pane (Phase 2 anchor) at top

### Phase 5 — Bugs Found Above
Roll all data-accuracy fixes (mislabel, drift, ambiguity) into this pass.

### Phase 6 — Mobile Pass
Currently sidebar collapses to top bar on mobile but pages aren't tuned. Quick wins:
- Hide HeroBand right-slot, stack vertically
- Tasks list — single column, bigger touch targets
- Chat works but ChatInput input bar needs viewport-bottom anchoring fix

### What I'm NOT doing this round
Based on prior rejection:
- ❌ Phosphor bloom on hero numbers (felt gimmicky)
- ❌ Chromatic split on text (broke calm tone)
- ❌ Pulsing animations
- ❌ Color palette overhauls (amber ARMED, indigo user) — rejected as wrong colors
- ❌ Adding new widgets (TracesHero) before existing ones are clean

**Kept from prior pass:** body-wide phosphor breathing flicker (user said fine).

---

## OSS Agent Inspiration (concrete moves to graft)

From `reference_oss_agents.md` and `project_oss_takeaways.md` (auto-memory):

### Pi (github.com/earendil-works/pi)
- **Config-hook agent loop** — `Agent.chat()` + `Agent.chat_stream()` are two near-identical 100-line functions. Refactor into one `_run_loop()` with hooks `transformContext`, `prepareNextTurn`, `shouldStopAfterTurn`. Single source of truth for the loop; new validators/state plug in cleanly.
- **Output guard** — `coding-agent/src/core/output-guard.ts` validates assistant output *before* it reaches the user. Add `core.py:output_guard(reply, context) -> reply | RejectReason` with cheap deterministic rules: leaked memory filenames, `example.com` fetches, claims about files we never read, tool error strings.
- **Compaction as subsystem** — Pi has `core/compaction/` directory. We have inline `_maybe_compact()`. Not urgent but enables tiered memory.

### Letta / MemGPT (github.com/letta-ai)
- **Memory-as-tools** — Letta exposes `archival_memory_search`, `core_memory_append`, `conversation_search` as actual tools. Replace our `_inject_relevant_memory()` with explicit `recall(query)` tool. Lets the agent decide *when* it needs context.
- **Three-tier memory** — Core (always in-context, small) / Recall (conversation history, searchable) / Archival (vector store, cold). We have one tier (markdown vault) + ad-hoc dump. Promote a small "core block" (current user facts + active project + recent notification) always in-prompt.
- **`conversation_search` tool** — search the daily log files we already write. Already added in an earlier PR; verify it's surfaced in the tool catalog.

### Hermes Agent (Nous Research)
- **Skill-library loop** — execute → evaluate → extract → refine → retrieve. We have skill_*.md files and a daily reflection. Missing: the *evaluate* step. Add success/failure feedback signal stored on the skill, increment on reuse, demote on failure. Currently skills are write-once.
- The "skill library" framing oversells what is fundamentally prompt templates with a usage counter — borrow the shape, don't build a whole subsystem.

### OpenClaw (github.com/openclaw/openclaw)
- **Schema-validated tool boundaries** — both Pi and OpenClaw validate tool arguments with a Zod-like schema at the LLM↔code boundary. We have `tools.SCHEMAS` (JSON schema) but don't validate at runtime; we just `json.loads()` and pass through. Add a validation pass; on failure hand the LLM a structured error and let it retry.
- **Discriminated unions over freeform strings** — anywhere we pass `status: string` between layers, make it a literal union.

### Mem0, Cognee
- Drop-in memory layers (vector + graph). **Don't adopt yet** — markdown vault is still fast enough. Revisit if the index ever crosses ~1000 entries.

---

## Functional Improvements (after visual pass)

Ranked by user-impact × ease:

1. **Telegram + Web Chat unified context** — currently each transport has its own conversation thread. A user message in Telegram is invisible to Web Chat. Pi/Letta treat conversations as one stream. (Pre-req: agree on session-key semantics across transports.)
2. **Memory recall inline in Chat** — `[[name]]` links are already stored; render as clickable chips with side-panel preview. Echoes Notion's backlinks.
3. **"Run now + stream-in-place"** on Tasks — click ARMED row → side panel streams the run live. Removes the trip to Traces.
4. **Skills as visible programs** — Tools page already has a list, but doesn't show *when* each skill fires. Add `last_used`, `success_rate`, "try this skill" runner.
5. **Memory hygiene** — surface stale memories (no read in 30+ days) with a one-click forget. Memory grows; cleanup is currently chat-only.
6. **Provider observability** — small row in sidebar: `gemini ✓ · kimi 429 · qwen ok`. Already tracked; just surface.
7. **Output validator** (Pi pattern) — see OSS section. Removes ~40% of the prompt-rule patches currently in CLAUDE.md.
8. **Mobile** — pocket-grade Overview (countdown + last action + open-chat CTA). Makes this a real daily driver.

---

## Suggested Execution Order

1. **Phase 5 bugs first** (10 listed above) — most are 1-line fixes, immediate user-visible improvement, zero risk of regressing the aesthetic.
2. **Phase 1 editorial restraint** — touch every page once, subtractive only. Big perceived quality jump for ~1 day of work.
3. **Phase 2 hero anchors** — promote countdown / demote wordmark / NOW pane for Chat.
4. **Phase 3 page density** — Tools tabs, Traces collapse-by-default, Overview readiness compact.
5. **Phase 4 Chat redesign** — the single biggest visual improvement, last because it's the largest surface.
6. **Phase 6 mobile** — once desktop is at 9/10.

After visual lands at ~9, functional pass:
1. Telegram + Web unified context (#1)
2. Memory recall in chat (#2)
3. Output validator (#7) — biggest harness-quality win
4. Skills program view (#4)
5. The rest as time permits

---

## What I Need From You Before Starting

1. **Confirm aesthetic direction** — restraint-first is right? Or do you want me to push something else?
2. **Confirm which page to start with** — recommendation: **Phase 5 bugs in a single PR** (low-risk, high signal), then we decide Phase 1 sequencing.
3. **`/` vs `/overview` redundancy** — pick one as the dashboard. Recommendation: kill `/` and make Home redirect to `/overview` (one less surface to maintain).
