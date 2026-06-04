# Homunculus — Capability Roadmap

**Date:** 2026-06-04
**Goal:** take Homunculus from "robust personal exercise" to "peer-tier OSS personal-assistant agent" — comparable to OpenClaw / Pi / Hermes / Letta for the **autonomous self-hosted daily assistant** use case.

**Source research:** `docs/ROBUSTNESS_PLAN.md` (completed), session research 2026-06-04 (web search across the four reference projects + Manus/Manus-AI usage patterns + HN AI-daily-tools threads).

---

## Scope locked in this session

User explicitly opted into the items below. Each is sized in **focused-day units** (1d = one uninterrupted day; expect calendar drift).

### Tier 1 (all)
- T1.1 WhatsApp transport — biggest daily-use multiplier *(~1 d)*
- T1.2 Skill auto-refinement on failure *(~1 d, structural)*
- T1.3 Daily-brief-as-a-task — flagship autonomous feature *(~½ d)*
- T1.4 Run-now + stream-in-place on Tasks *(~½ d, UX leap)*

### Tier 2 — selected
- T2.5 Browser automation (Playwright-MCP) *(~2 d)*
- T2.7 `AGENTS.md` identity file *(~½ d)*
- T2.8 Model-swap mid-session *(~1 d)*

### Tier 3 — selected
- T3.10 Daily proactive nudges (Hermes-style learning loop) *(~2 d)*

### Visual gaps (all)
- V.1 Live "run a task now" panel — covered by T1.4
- V.2 Mission Control sparse cards on quiet days *(~½ d)*
- V.3 PWA + mobile install prompt *(~½ d)*
- V.4 Skill failure surfacing *(~½ d)*
- V.5 Memory graph view (render `related: [[...]]` links) *(~1 d)*
- V.6 Telegram ↔ Web Chat unified conversation log *(~1 d)*

**Total estimated effort:** ~12 focused days. Spread realistically across 3–4 calendar weeks.

---

## Security & safety review per item

### T1.1 WhatsApp — the risk-relevant one

**Real risks**

1. **Account ban risk** — WhatsApp doesn't expose a personal-account API. The de-facto standard library [Baileys](https://github.com/WhiskeySockets/Baileys) speaks the WhatsApp Web protocol via reverse-engineering. Meta's Terms of Service forbid automated use of personal accounts. Risk is empirically low for single-user automation (sending to yourself, replying to your own messages) but non-zero. Mass-messaging / cold-outreach / scraping contacts pushes the risk hard upward.

2. **Session compromise** — first pairing requires scanning a QR with your phone's WhatsApp app. The resulting session blob is stored on disk and is equivalent to a credential. Anyone with that file can impersonate your linked device until you revoke it.

3. **Outbound message leakage** — a buggy automation could send the user's calendar / passwords / chat history to the wrong contact. We already have this risk on Telegram; WhatsApp widens the attack surface only because the contact graph is larger.

4. **Replay / spoofing** — Baileys handles encryption, so a third party can't inject messages. The remaining risk is a compromised server (the heartbeat container) sending messages the user didn't authorise.

**Mitigations (built into the implementation, not optional)**

1. **Hardcoded recipient allowlist** in env: `WHATSAPP_ALLOWED_RECIPIENTS=+91XXXXXXXXXX,+91YYYYYYYYYY`. The `notify` tool refuses to send to any number not on the list. This is the **#1 control** — even a fully compromised agent can't message your boss.
2. **Linked-device only.** Never the primary device. User scans QR on their phone; the session can be revoked from "Linked devices" in WhatsApp anytime.
3. **Session blob at rest** stored under `workspace/whatsapp_session/`, mode 0600, never committed (already in `.gitignore` by extension).
4. **Rate limiting.** Reuse the existing chat-rate-limit pattern (10/min per recipient). A runaway loop costs at most one minute of nuisance, not a deluge.
5. **Plan-mode opt-out.** When the agent is in PLAN mode the WhatsApp send tool returns a structured refusal (same pattern we use for other mutating tools). User has to explicitly switch to BUILD for WhatsApp to actually deliver.
6. **Confirmation prompt on first install.** The setup wizard explicitly walks the user through the Baileys ToS situation and asks them to acknowledge the linked-device pairing — no silent "we just enabled WhatsApp on your account."
7. **Audit log.** Every outbound WhatsApp message gets a `tool_call` event with the recipient redacted to last-4-digits. Reviewable in Traces.

**Out of scope.** WhatsApp Business API support (requires Meta business verification — a different transport entirely; revisit if/when a real business case appears).

### T2.5 Browser automation security

Playwright runs a real Chromium binary. Risks:
1. **Credential exposure** if the agent navigates to a login page and types the user's credentials. → Mitigation: separate browser profile, no persistent cookies by default, the agent has to explicitly call a `browser_login(profile_name)` tool to attach a credentialed profile, and that tool is mutating (PLAN mode refuses it).
2. **Drive-by malware** if the agent fetches an attacker-controlled URL. → Mitigation: Playwright's process is sandboxed by Chromium; we already accept the same risk for `web_fetch`. No incremental risk.
3. **Resource exhaustion** — a misbehaving page can chew memory. → Mitigation: hard ulimit on the Playwright process; auto-kill after 60s of inactivity per page.

### T2.8 Model-swap mid-session security

Risk: a user accidentally routes a sensitive prompt to a less-private provider (e.g. switches to a free-tier model that logs prompts for training). Mitigation: `/use <model>` command in chat shows a one-line privacy badge before the next message goes out ("kimi-k2.6:free — provider may use prompts for training. Proceed? y/n"). Default models in our chain are already tagged.

### General security posture across all transports

Already in place from the robustness work:
- Output guard catches memory filename leaks in agent replies.
- TaskGuard refuses `complete_task` when criteria fail.
- Per-IP rate limiting on `/api/chat/send`.
- Plan mode for mutating tools.

Add as we ship the new transports:
- Recipient allowlist for any "outbound" tool (notify-whatsapp, notify-discord, etc.).
- Per-transport audit log in events.jsonl.
- A `panic_button` MCP tool the user can call from any transport that revokes every linked device + clears every cached session. One command, all transports.

---

## Per-item plan with file-level pointers

### T1.1 · WhatsApp transport

**Files to create/touch:**
- `transports/whatsapp.py` — Baileys via [`baileys-python`](https://github.com/orlandolatipo/baileys-python) wrapper or a Node sidecar (decide on Node sidecar — Baileys is mature and battle-tested in TS).
- `docker-compose.yml` — new `whatsapp` service (Node sidecar) sharing the workspace volume.
- `tools/notify_whatsapp.py` — new MCP tool `notify_whatsapp(text, recipient)`, allowlist-guarded.
- `tools/mcp_server.py` — register the new tool.
- `.env.example` — `WHATSAPP_ALLOWED_RECIPIENTS=` documented.

**Tests:** mocked-Baileys integration that asserts (a) send is rejected for non-allowlist recipients, (b) send to allowlisted goes through, (c) rate limit kicks in after 10/min.

### T1.2 · Skill auto-refinement on failure

**Pattern (from Hermes):** after every `record_failure` the heartbeat checks if the task has a corresponding `skill_<slug>.md` memory. If yes:
1. Read the skill's body
2. Compose: original skill + new "Watch out:" line citing the failure reason
3. `memory.remember` with the same name → overwrite

**Files to touch:**
- `heartbeat.py` post-tick check — extend the silent-drop block to also detect `record_failure` and trigger skill update
- New: `tools/_skill_refiner.py` — pure helper that composes the updated skill body
- Tests: a record_failure on a task with a matching skill produces a new memory write with the failure reason appended

### T1.3 · Daily-brief-as-a-task

**Skill content:** `skill_daily_brief.md` that defines the agent's morning routine:
1. Read calendar events for today (via web_fetch on a public iCal URL or a `calendar.md` file)
2. Read active tasks → format anything due today
3. Optional: fetch GitHub notifications via API
4. Compose ONE notify message: "Good morning. 3 things for today: ..."

**Bootstrap task:**
```python
create_task(
  title="Morning brief",
  due_at="<tomorrow 8am IST>",
  recurrence="daily",
  notify=true,
  success_criteria=[
    {"type": "notify_called"},
    {"type": "notify_min_chars", "n": 80},
    # No code-block requirement; this is prose.
  ],
)
```

**Files to touch:**
- `workspace/memory/skill_daily_brief.md` — write the skill
- One-time: a small migration script `scripts/bootstrap_daily_brief.py` that calls `create_task`
- Tests: a fake brief is composed with mocked tools; assert the message has the expected sections

### T1.4 · Run-now stream-in-place

**Files to touch:**
- `transports/web_api.py` — new endpoint `POST /api/tasks/{id}/run` that returns an SSE stream of the run's events
- `web/src/components/tasks/RunNowPanel.tsx` — new component, slides in from right, subscribes to the run SSE
- `web/src/pages/TasksPage.tsx` — wire RunNowPanel into the [run now] button
- Backend: leverage the existing heartbeat tick logic; the new endpoint just calls into it with a one-shot task list

**Tests:** API endpoint returns SSE; assert the standard event sequence ends with a `tool_result complete_task` or `task_failure`.

### T2.5 Browser automation

**Files to touch:**
- New `mcp-servers/playwright/` (already exists in many MCP repos — we can mount the [official Playwright MCP](https://github.com/microsoft/playwright-mcp))
- `homunculus.yaml` — register the new MCP server
- `Dockerfile` — install Playwright + Chromium dependencies in the heartbeat image (this adds ~1GB; consider a separate `homunculus-browser` image instead)
- Tests: agent can navigate to a static HTML test page and extract text

### T2.7 `AGENTS.md` identity file

**Pattern (from OpenClaw SOUL.md / Hermes AGENTS.md):** single Markdown file at repo root that's a) version-controlled, b) loaded into the agent's system prompt on every tick.

**Format:**
```markdown
# Identity
Name: ...
Tone: ...

# Always
- ...

# Never
- ...

# Allowed tools (subset of catalogue; remove to restrict)
- read_file
- notify
- ...
```

**Files to touch:**
- New: `AGENTS.md` at repo root with sensible defaults
- `core.py` — system prompt assembly reads AGENTS.md and prepends
- Tests: agent's history begins with the AGENTS.md content prepended

### T2.8 Model-swap mid-session

**Files to touch:**
- `core.py` — `chat_stream` accepts an optional `model_override` (already exists in `_run_loop`; just expose at the public method)
- `web/src/components/chat/BrutalistChatInput.tsx` — slash-command parser for `/use <model>`
- `web/src/lib/api.ts` — `chat_send` includes `model_override` field
- One-line privacy badge UI when switching to a free-tier provider

**Tests:** request with `model_override=kimi-k2.6:free` actually uses kimi; the displayed badge reflects the override.

### T3.10 Daily proactive nudges

**Approach:** add a new heartbeat phase "weekly reflection" that runs once per Sunday, reads `tasks/tasks.json` + last 7 daily logs, and decides whether to surface anything proactively. The output is a `notify` if there's something noteworthy, else silence.

**Skill:** `skill_weekly_reflection.md` defines what counts as "noteworthy":
- A task has failed 3+ times this week
- A skill hasn't been updated in 30+ days but its task has new failures
- A memory references something the user hasn't touched in 60+ days

**Files to touch:**
- `heartbeat.py` — add the weekly-reflection branch (mirrors the existing daily-reflection branch)
- New skill file
- Tests: simulate a week of failures; assert a notify is composed

### V.2 Mission Control sparse cards

**Fix:** when the latest-turn / operations panels would be empty, hide them and show a single compact "Next 3 fires" tile instead. Reuses task data we already fetch on Overview.

### V.3 PWA + mobile install prompt

**Files to touch:**
- New: `web/public/manifest.json` — name, icons, theme color (existing brutalist phosphor)
- New: `web/public/sw.js` — basic offline-friendly service worker (cache the shell)
- `web/index.html` — link the manifest, register the SW
- `web/src/components/layout/InstallBadge.tsx` — `beforeinstallprompt` listener + a small "[ install ]" badge in the sidebar

### V.4 Skill failure surfacing

**Files to touch:**
- `transports/web_api.py` — `/api/skills` already returns `failure_count`; ensure recent failures are exposed
- `web/src/pages/SkillsPage.tsx` (Tools/Catalog tab) — add a "Needs attention" subsection at the top for any skill with `failure_count > 0` in the last 7 days

### V.5 Memory graph view

**Files to touch:**
- `web/src/components/memory/MemoryGraph.tsx` — new component, force-directed via [`@vis-network/standalone`](https://github.com/visjs/vis-network) or `d3-force`. Nodes = memory files, edges = `related: [[name]]` references already stored in frontmatter.
- `web/src/pages/MemoryPage.tsx` — toggle between list and graph views

### V.6 Telegram ↔ Web Chat unified log

**Files to touch:**
- `transports/telegram.py` — mirror incoming/outgoing messages into the same SSE event stream the Web UI reads
- `web/src/pages/ChatPage.tsx` — render Telegram messages with a small "via TG" badge so the source is visible
- Tests: a Telegram-originated message shows up in the Web Chat log within 1s of being received

---

## Unfinished items from prior plans (audit)

Checked against:
- `docs/UI_AUDIT.md` (UI/UX session, 2026-06-03 → 04)
- `docs/ROBUSTNESS_PLAN.md` (agent robustness session, 2026-06-04)

### Robustness — fully shipped except:
- **Full `_run_loop` refactor with 4-hook LoopConfig** — deferred. The pragmatic slice (single `set_pre_turn_hook`) carries today's needs. The full refactor would let us add `on_tool_result` (used by item 6 auto-offload) and `transformContext` (used by future compaction hooks).
  - **Decision:** keep deferred. Cost is 2 days, benefit lands only when a future hook needs it. Revisit when item 6 grows an auto-archive feature.

### UI/UX — fully shipped from Phases 1, 3, 4, 5, 6 except:
- **Phase 1 editorial restraint pass** — only the highest-impact items shipped (sidebar dashes, Overview text-shadow audit). The broader "audit every component for noise" sweep was deemed lower-priority once Tasks/Memory/Tools/Traces hit 8+.
  - **Decision:** keep deferred. Will revisit before any external launch.
- **Tooltip basic quality** — user flagged "improve later." See: `ContextStatusCell`, `PageHeader` HMCL-01 badge, ARMED chip on Tasks.
  - **Decision:** roll into the V.2/V.4 visual pass — replace `title=` with a properly-styled hover popover.
- **Chat full operator-pane redesign (Phase 4 deferred)** — only the NOW pane + width reduction shipped. Per-turn console framing didn't.
  - **Decision:** keep deferred. Wait until WhatsApp ships so Chat is no longer the primary surface for daily use; then revisit with calmer constraints.

### From the robustness audit acceptance criteria — verifying

✓ The LeetCode tick that failed today reproduces successfully — **verified live** 2026-06-04 08:18 (Rotate Array delivered).
✓ No regression in existing test suite — 162 pass.
✓ Weekly `agent_didnt_complete` will drop to ≤1 — need 7 days of observation; trending early signal: silent drops in last 12 hours dropped from 3/hour (Jun 3) to 0 post-fix.
~ History token count grows ≤ linearly — partial; item 6 archival is the API, auto-offload would close this fully.

---

## What we're explicitly NOT building (yet)

Documented so future-us doesn't relitigate:

1. **WhatsApp Business API** — different transport entirely; requires Meta business verification. Personal Baileys for personal use is the right scope.
2. **Voice wake-word.** OpenClaw / Hermes both have this. Our window is voice-notes-via-Telegram (T3 in original list, dropped for now) — cheaper, covers actual daily use without a wake-word UX investment.
3. **Multi-agent routing (OpenClaw pattern).** Single-user single-agent is the right primary shape. Multi-agent revisits if/when we have a real use case.
4. **Custom LLM training / fine-tuning.** Hermes uses ShareGPT trajectory generation. Out of scope for a single-user assistant.
5. **Marketplace for skills.** The community templates pattern OpenClaw uses (`awesome-openclaw-agents`) is great but requires real community first.

---

## Suggested execution order

Adapted from "one focused week" in the research; the user's selected scope is ~12 days so it's two weeks calendar.

**Week 1 — daily-use multipliers**
1. T1.4 Run-now stream-in-place *(½ d)*
2. T1.3 Daily-brief task *(½ d)*
3. T1.1 WhatsApp transport *(1 d, security checklist mandatory)*
4. T1.2 Skill auto-refinement *(1 d)*
5. V.4 Skill failure surfacing *(½ d)*

**Week 2 — depth + polish**
6. T2.7 AGENTS.md identity *(½ d)*
7. T2.8 Model-swap mid-session *(1 d)*
8. T2.5 Browser automation *(2 d)*
9. T3.10 Weekly proactive nudges *(2 d)*
10. V.2 Mission Control fix *(½ d)*
11. V.3 PWA + install *(½ d)*
12. V.5 Memory graph view *(1 d)*
13. V.6 Telegram-Web unified log *(1 d)*

Buffer day for integration/regressions = day 14.

---

## Success metrics

Trailing 7 days after the roadmap lands:
1. **At least 1 task per day fires & completes via WhatsApp** (T1.1 + T1.2 + T1.3)
2. **Zero silent drops** in the heartbeat events log
3. **Skill failure_count for every active task ≤ 1 in the last 7 days** (T1.2 working)
4. **User opens Web Chat ≤ 3 times** but receives ≥ 7 WhatsApp messages — proves the autonomous multiplier
5. **PWA install** on the user's phone (V.3) — qualitative but visible

If those five hold, Homunculus is genuinely "useful for daily use" by the standard the OSS literature sets.
