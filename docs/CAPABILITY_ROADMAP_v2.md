# Homunculus — Capability Roadmap v2

**Date:** 2026-06-05
**Supersedes:** `CAPABILITY_ROADMAP.md` (v1, 2026-06-04)
**Why a v2:** v1 shipped 11 of 12 items. Research on WhatsApp ban risk in
2026 (Baileys/WhatsMeow account terminations, ~2.76% appeal success rate,
ToS violation) made T1.1 untenable for a personal-number deployment.
This v2 drops T1.1 and replaces it with the items that actually move
Homunculus toward peer-tier OSS personal-assistant capability.

---

## What shipped from v1 (kept as record)

- ✅ T1.2 Skill auto-refinement on failure (PR #73)
- ✅ T1.3 Daily-brief-as-a-task (PR #72)
- ✅ T1.4 Run-now + stream-in-place on Tasks (PR #71)
- ✅ T2.7 AGENTS.md identity file (PR #75)
- ✅ T2.8 Model-swap mid-session via `/use` (PR #78)
- ✅ T3.10 Weekly proactive nudges (PR #83)
- ✅ V.2 Mission Control sparse cards (PR #80)
- ✅ V.3 PWA + mobile install (PR #77)
- ✅ V.4 Skill failure surfacing (PR #74)
- ✅ V.5 Memory wikilinks (PR #81)
- ✅ V.6 Telegram ↔ Web Chat unified log (PR #82)
- ✅ Bonus: tool-result eviction MemGPT-style (PR #79)

## What was dropped

- ❌ **T1.1 WhatsApp transport.** Research showed:
  - Baileys/WhatsMeow accounts ban-terminated in 2–8 weeks typical
  - Linked-device pairing warnings now propagate to the primary account
  - 2.76% appeal success rate (Meta India transparency report, Feb 2026)
  - Cloud API is the only safe path; requires a separate phone number,
    Meta business verification, restrictive 24h-window template rules
  - Telegram already covers the "phone-first nudge" channel reliably
  - **Verdict:** dropped. The risk to the user's primary identity is
    not justified by the marginal channel value over Telegram.

---

## v2 scope — locked in this session

User explicitly opted into the four items below. Sized in focused-day
units. Total: ~8 focused days.

### NEW.1 — Calendar + email read integration *(~2 d)*

**Why:** the daily brief currently knows tasks but not the user's actual
day. "You have 3 things at work" is wallpaper; "Standup at 10, lunch
with Y at 13, 4 unread from Z" is information.

**What:**
- Read-only Google Calendar + Gmail tools mounted via MCP
- OAuth flow with refresh-token persistence (no embedded creds)
- Scopes: `calendar.events.readonly`, `gmail.readonly`. Read-only is
  a hard rule for v1 — no event creation, no mark-as-read, no send.
- New skill `skill_inbox_triage.md` for "summarise unread from people I
  actually reply to" — uses contact recency from the recall index.

**Security:** OAuth tokens live in `workspace/google_tokens.json`, mode
0600, gitignored. Scopes are read-only; even a fully compromised agent
cannot mutate the user's calendar or mail.

**Files touched:** `tools/google_calendar.py`, `tools/google_gmail.py`,
`tools/__init__.py` to register, `memory/skill_inbox_triage.md`
embedded in a new bootstrap script.

### NEW.2 — Memory hierarchy (Letta-style paging) *(~3 d)*

**Why:** we have flat typed memory + tool-result eviction + summarisation.
Letta's appeal is the OS-virtual-memory metaphor: core block (always
in context), recall (recent, paged in/out), archival (queryable on
demand). Long threads currently compress lossily; paging extends
usable session length without losing nuance.

**What:**
- `memory/_core.md` — small always-present block (1KB cap) read on
  every turn. Identity facts, current focus, the rules the user has
  set today. Edits via a `core_edit` tool.
- Existing `recall(query)` and `archival_memory_search(query)` already
  function as the paging API; promote them to first-class tools with
  clearer docs.
- Compaction (today: summarise older user-turn band) gains a "promote
  to archival" pass — extracted facts land in archival as searchable
  entries instead of collapsing to a one-shot system summary.
- Tool: `memory_promote(entry_id)` — the agent can flag a chat moment
  for archival without waiting for compaction.

**Security:** archival entries get the same `source` tagging as chat
messages (V.6). User can review/delete via the memory UI.

**Files touched:** `core.py` (compaction + core block injection),
`memory.py` (promote API), `tools/memory.py`, `memory/_core.md`
template (write via bootstrap script).

### NEW.5 — iOS Shortcuts quick-capture *(~½ d)*

**Why:** lowest-latency way to add a task or note from a phone. "Hey
Siri, tell Homunculus I need to call dentist Friday" → task created.

**What:**
- `POST /api/quick-capture` — auth via X-Capture-Token (separate from
  web cookie, rotatable). Body: `{text: string, kind?: "task"|"note"}`.
- Server-side: the text is fed to a single-turn agent call with a
  narrow tool set (`create_task`, `archival_memory_insert`). Returns
  a short confirmation that Siri reads back.
- Shortcut JSON template in `docs/ios_shortcut.md` so the user can
  import once. Includes the X-Capture-Token field.

**Security:** dedicated token (not the web auth cookie) so a leaked
Shortcut config can't open the full dashboard. Rate-limited (5/min).

**Files touched:** `transports/web_api.py` endpoint, `docs/ios_shortcut.md`.

### T2.5 — Browser automation (Playwright-MCP) *(~2 d)*

**Why:** the morning brief implicitly needs the live web ("check the
weather", "is the gym open today", "what time does X open"). `web_fetch`
handles plain HTML; JS-heavy sites need a real browser.

**What:**
- Mount Playwright-MCP as a sibling service in `docker-compose.yml`
- Tools: `browser_navigate`, `browser_snapshot`, `browser_click`,
  `browser_type`, `browser_evaluate` exposed via MCP
- Skill `skill_live_lookup.md` documents when to use browser vs
  web_fetch (rule of thumb: prefer web_fetch first; escalate to
  browser only if the page is JS-rendered or requires interaction)

**Security:**
- Separate browser profile per session, no persistent cookies by default
- Hard ulimit on the Playwright process; auto-kill after 60s idle
- Login tool (`browser_login`) is gated behind PLAN-mode refusal —
  user has to explicitly authorise credentialled sessions
- Same `tool_call` audit log as every other tool

**Files touched:** `docker-compose.yml`, `homunculus.yaml` (MCP mount),
`memory/skill_live_lookup.md`.

---

## Out of scope for v2 (revisit later)

- **Voice in/out via Telegram (STT/TTS).** Was on the table but adds
  external API dependencies (Whisper or Gemini live audio). Defer until
  the new core ships and we know what cadence the user actually wants.
- **Shareable skill packages.** Cool but premature — we're still
  iterating on our own skills. Don't standardise a sharing format
  until our internal format is stable.
- **Multi-channel beyond Telegram + Web.** Slack, Discord, Signal,
  iMessage — none fill a real gap. Skip.
- **WhatsApp Cloud API.** Possible future if a real business use case
  emerges (e.g. clients message the agent). Personal use does not
  justify the Meta-business-verification overhead.

---

## Order of execution

Smallest+highest leverage first:

1. **NEW.5 iOS Shortcuts** *(½ d)* — small, immediate daily-use win,
   no architectural surface
2. **T2.5 Browser** *(2 d)* — extends what the daily brief can know;
   no shared state with the rest of the system
3. **NEW.1 Calendar + Email** *(2 d)* — biggest functional unlock for
   the morning brief; OAuth setup is the hard part
4. **NEW.2 Memory hierarchy** *(3 d)* — biggest technical change;
   land last so the upstream items don't perturb the design

---

## Definition of done per item

- Code merged via PR (per CLAUDE.md workflow)
- New tools have schema validation + at least one test
- New skills land in `memory/` via a bootstrap script (workspace/
  memory is gitignored — bootstrap is how every install gets seeded)
- Roadmap entry above ticked ✅ with PR number
