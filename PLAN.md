# PLAN — Homunculus

Single source of truth for in-flight work and the queue. Update as
items move. No log entries — when something ships, move it to **Done**
with one line. When the queue gets stale, prune.

---

## In flight

_(nothing — pick the next item from the Queue below)_

### Tasks page — DONE ✓

**Goal.** Make every scheduled task visible. Today recurring work
hides in `workspace/tasks/tasks.json`; only the agent sees it. This
page surfaces what's queued, what's recurring, what fired last and
when. Doubles as the reliability surface — "this task has fired 14
times, 13 succeeded."

**Design principle.** Build for the SOTA-model ceiling. Rich task
schema, full last-run history, no compromises that only make sense
because today's free models are weak.

#### Backend

Extend `tasks.py`:
- Add `last_runs: list[RunRecord]` to each task. `RunRecord` =
  `{ ts, duration_s, status: "success"|"failure", result: str }`.
- `complete_task(id, result)` appends to `last_runs` (cap at 20 most
  recent).
- New helper `record_failure(id, error)` for the heartbeat to call if a
  due task throws.

New endpoints in `feed.py`:

```
GET    /api/tasks                       list all (filter by status)
POST   /api/tasks                       create
PATCH  /api/tasks/{id}                  edit title / desc / due / recurrence
POST   /api/tasks/{id}/complete         mark complete (advances recurrence)
POST   /api/tasks/{id}/cancel           cancel
POST   /api/tasks/{id}/run-now          set due_at=now, heartbeat fires next tick
DELETE /api/tasks/{id}                  hard delete
```

#### Frontend

- Add `/tasks` route.
- `TasksPage.tsx`: header (counts + "New task" button), grouped lists
  (Active / Completed / Cancelled), `TaskRow` per item.
- `TaskRow`: title · cadence · next due · success rate · inline
  actions (edit / run now / cancel / delete).
- `TaskDetailDrawer`: slide-in showing description + last 10 runs.
- `TaskForm`: create + edit modal.
- Add nav item "Tasks" in `Header.tsx` between Activity and Memory.

#### Files

```
NEW   web/src/pages/TasksPage.tsx
NEW   web/src/components/tasks/TaskRow.tsx
NEW   web/src/components/tasks/TaskDetailDrawer.tsx
NEW   web/src/components/tasks/TaskForm.tsx
EDIT  web/src/App.tsx                       add /tasks route
EDIT  web/src/components/layout/Header.tsx  add nav item
EDIT  web/src/lib/api.ts                    7 new endpoints
EDIT  web/src/lib/types.ts                  Task + RunRecord
EDIT  feed.py                               endpoints
EDIT  tasks.py                              last_runs field + helpers
EDIT  heartbeat.py                          call record_failure on tick error
```

#### Sizing

~3h: 45m backend, 2h frontend, 15m polish/test.

---

## Queue (next, in priority order)

1. **UI polish pass** — the current dashboard is "competent template"
   per user. Needs Awwwards-tier: signature element that earns the
   product's name (heartbeat metaphor), one dominant hero per page,
   asymmetric layouts, bespoke SVG viz instead of stacked bars, big
   typographic anchors (>48px), microcopy with voice. See
   feedback_homunculus_ui_bar memory.
2. **Live page** — real-time "agent's computer" pane. Tools running,
   model thinking, tokens streaming, memory loading. Currently a
   placeholder.
3. **End-user tutorial** — `USAGE.md` (or in-app onboarding) covering:
   how to talk to the agent, what to trust it with, how to set
   recurring tasks, how to inspect memory, how the dashboard surfaces
   reliability. Written for the user, not the builder (LEARN.md is for
   the builder).
4. **Gateway daemon.** Long-running WebSocket router. Transports
   become thin clients. Sessions key on (user, channel) — fixes the
   Telegram + Web session interleaving bug.

---

## SOTA-readiness invariants

When making any change, check these don't regress:

- **MAX_TURNS ≥ 20.** Don't cap low because today's model loses track.
- **Tool schemas keep rich descriptions.** Detail helps SOTA models, is
  ignored by weak ones — never strip.
- **Parallel tool calls supported in the loop.** OpenAI spec allows it;
  honor `tool_calls: list` length > 1.
- **Stream interruption is first-class.** Build cancel tokens, not
  best-effort.
- **System prompt stays detailed.** Don't truncate for free-tier
  context limits.
- **Tool isolation is real.** Sandbox per-call where possible — more
  agency means higher blast radius.

---

## Done

- **Useful extension pass** — branch adds three budget-safe usefulness
  upgrades: deterministic memory consolidation proposals (duplicates /
  stale project memories file human-gated `memory_delete` proposals,
  never direct deletes), grouped run cards on Traces backed by the
  existing replay/event log builder, and skill contract tests for live
  registry validation (filename/name matching + missing tool refs).
  Stale docs preserved under `docs/archive/`; root `IDEAS.md` is now
  the live budget-first idea list.
- **MCP end-state architecture** — replaced direct tool dispatch with
  a multi-server MCP manager. `homunculus.yaml` declares servers; the
  manager (`tools/mcp_manager.py`) launches each as a subprocess over
  stdio and holds a persistent `ClientSession`. The builtin server
  (`tools/mcp_server.py`) is a FastMCP module — each tool is an
  `@mcp.tool()`-decorated function with `Annotated[..., Field(...)]`
  for rich per-parameter descriptions and `annotations={readOnlyHint:
  ...}` driving plan-mode policy. The per-category modules
  (`filesystem`, `memory_tools`, `web`, `sandbox`, `scheduling`,
  `notify`) shed their `SCHEMAS`/`TOOLS` dicts — single source of
  truth is the FastMCP server. External servers join via the same
  manager — `mcp-server-fetch` wired and verified, surfacing as
  `fetch__fetch`. Tool name namespacing: builtin unprefixed,
  externals prefixed `{server}__{tool}`. Per-tool plan-mode policy
  reads `readOnlyHint` from MCP annotations (server-level
  `mutating: false` is the upper bound). Hot reload via `watchfiles`
  on `homunculus.yaml`: each server runs in its own asyncio task
  with a stop event, so `manager.reload()` diffs config and starts/
  stops/restarts servers without disturbing the others. Skills page
  sees all 16 tools through MCP. Verified end-to-end in Docker.
- **Pi-spirit slim** *(Pi-inspired)* — system prompt rewritten:
  4400→1887 chars (57% smaller). Dropped per-tool descriptions (already
  in schemas), repeated memory-type lists (already in schema enum), and
  prose hygiene rules; kept only load-bearing protocol (paths, memory
  loop, recurring-task instinct, behaviour notes). Added the
  self-extension pattern: `workspace/scripts/` directory + feedback
  memory teaching `write_file → read_file → python` for reusable
  Python. Sandbox stays read-only; persistence comes from the
  workspace volume. Pi philosophy applied without autonomous bash.
- **Run abort in chat** — client passes a `stream_id` with each chat
  send; server tracks active streams; `POST /api/chat/cancel
  {stream_id}` flags the stream for cancellation; the SSE generator
  checks between chunks and exits with `[stopped by user]`. Send →
  Stop button swap in ChatInput. Verified end-to-end: 120-chunk stream
  cancelled mid-flight, clean done event.
- **Overview page** (`/`) — new home with HeartbeatRibbon (24h
  stacked-bar activity), HeartbeatPulse in sidebar (living indicator),
  4 stat tiles (Actions / Replies / Model calls / Failures today),
  Up Next list, Recent Activity. Bioluminescent mint accent replaces
  indigo. Geist added for display, Inter stays for body.
- **Skill memory type + reflection upgrade** *(Hermes-inspired)* —
  added `skill` as a fifth memory type alongside user / feedback /
  project / reference. `memory.py` ALLOWED_TYPES extended; `remember`
  tool schema enum updated. Reflection prompt in `heartbeat.py` now
  instructs the agent: when it successfully completes a non-trivial
  multi-step workflow, write a `skill_*` memory with Trigger + numbered
  Steps so future-you can replay. Seeded with
  `skill_deliver_daily_leetcode.md` showing the format. MemoryCard and
  ProcessRail render the new type with a distinct color. Verified
  `remember(type="skill")` → `skill_*.md` file + index entry.
- **Plan/Build dual-mode** *(opencode-inspired)* — `tools/_state.py`
  gains `_mode` + `get_mode()` / `set_mode()`. `execute()` refuses
  calls to mutating tools (`write_file`, `remember`, `forget`, `python`,
  `shell_exec`, `notify`, all scheduling tools) when mode == "plan",
  returning a structured `BLOCKED (plan mode): ...` message so the
  agent describes the action instead. `GET/POST /api/mode` endpoints.
  UI: toggle in the Header + persistent banner under the header when
  in plan mode. Verified end-to-end (read_file allowed, write_file
  blocked, build switch restores access).
- **Skills page** — `/skills` route. Aggregates per-tool stats from
  `tools.SCHEMAS` + `_events.jsonl`: name · description · call count ·
  success rate · last used. Grouped into "Used" + "Never used".
  Auto-refreshes every 30s. Answers "what does this agent reliably do?"
- **transports/ rename** — `main.py` → `transports/repl.py`,
  `telegram_bot.py` → `transports/telegram.py`, `feed.py` →
  `transports/web_api.py`. Dockerfile copies the package; compose runs
  each service via `python -m transports.X` with `PYTHONPATH=/app`.
  All four containers come up clean on the new layout; FastAPI now
  serves the SPA from `transports.web_api:app`.
- **tools.py → tools/ package** — split 827-line monolith into
  `_state.py`, `_helpers.py`, and 6 category files (filesystem,
  memory_tools, web, sandbox, scheduling, notify). Each category
  exposes its own SCHEMAS + TOOLS; `__init__.py` aggregates and
  exposes the public API (`init`, `execute`, `SCHEMAS`, `TOOLS`).
  Same external surface — `import tools` keeps working. 15 schemas,
  15 dispatch entries, verified.
- **Tasks page** — `/tasks` route. List active/completed/cancelled,
  detail drawer with last-runs history, create form, run-now / cancel /
  delete actions. Backend: `tasks.py` gains `last_runs`, `update()`,
  `record_failure()`, `delete()`, `run_now()`. Heartbeat records
  failures on tick errors.
- LEARN.md rewritten as tutorial-style reference (~430 lines)
- Four bug fixes: error-message filter in `save_session`, recurring-
  task instinct memory, recurring-delivery pattern memory + leetcode
  instance, chapter close UX (no reload, toast + animated transition)
- Pattern/instance separation for delivery tracking
- SSE buffer client-side dedup
- httpx ResponseNotRead fix in `_is_transient_provider_error`
- Bookmark ribbon redesigned as quiet side toggle
- Medieval theme stripped → quiet EB Garamond editorial
- Latin labels → English technical names
- Vite dev server set up for HMR on `:5173`
- Multi-provider fallback chain (Groq → Gemini → OpenRouter → Cerebras)
