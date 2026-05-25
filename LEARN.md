# LEARN.md — Homunculus

> A tutorial for building a self-hosted autonomous AI agent from scratch.
> Persistent memory, multiple channels, a heartbeat, no frameworks,
> free LLM tier compatible.
>
> The goal is **learning**: you should be able to rebuild this from
> first principles after reading this doc. Code references are to the
> actual files in the repo; numbers in brackets like `[core.py:120]`
> point you at the line worth opening.

---

## 1. The Mental Model

**An agent is a `while` loop around an LLM API call, where the LLM is
allowed to request that you run functions on its behalf.**

That's the entire idea. Three things follow:

1. **The LLM never executes anything.** It emits text — including text
   that says *"please run `read_file({"path":"notes.txt"})`"*. We are
   the hands; the model is the brain.
2. **The loop terminates when the LLM returns text without a tool
   call.** That's the only exit condition.
3. **The LLM is stateless.** Every request resends the entire history.
   We accumulate it. *We are the memory.*

Once those three click, every "agent framework" you'll ever see is
elaboration on this ~30-line loop.

---

## 2. Architecture (current and target)

Every serious agent — OpenClaw, Manus, Devin — has the same three
layers. Homunculus matches the shape, with one big gap: tools are
hardcoded Python functions, not pluggable MCP servers. The roadmap in
§10 closes that gap.

```
┌─────────────────────────────────────────────────────────┐
│  TRANSPORTS  Telegram · Web · REPL · (future: Slack)   │
└────────────────────────┬────────────────────────────────┘
                         │ shared workspace volume today
                         │ (will be: WebSocket → Gateway)
┌────────────────────────▼────────────────────────────────┐
│  AGENT RUNTIME                                          │
│  intake → context assembly → LLM → tool execution →    │
│  stream → persist (memory + events + session)          │
└────────────────────────┬────────────────────────────────┘
                         │ today: Python function dispatch
                         │ target: MCP protocol
┌────────────────────────▼────────────────────────────────┐
│  TOOLS / SKILLS                                         │
│  filesystem · memory · web · sandbox · scheduling …    │
└─────────────────────────────────────────────────────────┘
```

Outside this stack, two background concerns:
- **Heartbeat** — a daemon that wakes the agent every N minutes (or at
  a scheduled time) to do work without a user prompt. See §7.
- **Event log** — every service appends to `workspace/_events.jsonl`.
  Single source of truth for the live UI and for debugging.

---

## 3. Project Layout

```
homunculus/
├── pyproject.toml        # uv-managed deps
├── docker-compose.yml    # one container per service
├── Dockerfile            # multi-stage: web bundle → python runtime
├── .env.example
├── core.py               # Agent class + LLM client + fallback chain
├── memory.py             # Markdown-frontmatter memory store
├── tasks.py              # Structured tasks (tasks.json)
├── events.py             # Shared event log writer
├── heartbeat.py          # Background autonomy daemon
├── tools/                # tool registry + implementations (§6)
├── transports/           # repl.py, telegram.py, web_api.py
├── web/                  # React + Vite SPA
├── workspace/            # mounted volume (memory, sessions, events)
└── LEARN.md              # ← you are here
```

Each top-level Python file or package has one job. Read in this order
the first time: `core.py` → `tools/` → `memory.py` → `heartbeat.py` →
`transports/`.

---

## 4. The Agent Loop

`Agent.chat()` returns a final string. `Agent.chat_stream()` yields
content chunks for real-time display. Both are thin wrappers around a
single shared generator `Agent._run_loop()`:

```python
def chat(self, user_text: str) -> str:
    return "".join(self._run_loop(user_text, streaming=False))

def chat_stream(self, user_text: str):
    yield from self._run_loop(user_text, streaming=True)
```

`_run_loop()` stripped to its essence:

```python
def _run_loop(self, user_message, streaming):
    self.history.append({"role": "user", "content": user_message})
    tool_names_used = set()

    for _ in range(MAX_TURNS):
        if streaming:
            # Yields content chunks in real-time; assembles assistant_msg.
            assistant_msg = yield_from_stream(...)
        else:
            assistant_msg = call_llm(self.history, tools.SCHEMAS)

        self.history.append(clean(assistant_msg))

        if not assistant_msg.get("tool_calls"):
            # Final reply — run output guard before returning.
            reply = self._output_guard(assistant_msg["content"], tool_names_used)
            yield reply
            return

        for call in assistant_msg["tool_calls"]:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            tool_names_used.add(name)

            # Schema-validate args before dispatch.
            if err := _validate_tool_args(name, args):
                result = f"ERROR: bad args for {name}: {err}. Retry."
            else:
                result = tools.execute(name, args)

            self.history.append({"role": "tool", "content": result, ...})
```

**Why one loop instead of two?** When `chat()` and `chat_stream()` were
separate methods (~100 lines each), any bug fix or new feature had to
land twice — and they drifted apart. Single code path = bugs fixed once,
everywhere.

### The output guard

Every final reply passes through `Agent._output_guard()` before reaching
the user. It catches four deterministic failure modes:

| Rule | What it catches | Example |
|---|---|---|
| `memory_filename_leak` | Internal `*.md` filenames in the reply | `project_foo.md does not exist` |
| `internal_path_leak` | `workspace/memory/` paths in the reply | `memory/logs/2026/05/...` |
| `error_echo` | LLM echoed a tool ERROR verbatim as its reply | `ERROR: read_file timed out` |
| `example_com_confabulation` | example.com cited without a web tool call | The "Explain" → example.com bug |

If any rule fires, the reply is replaced with a safe fallback and an
`output_guard` event is emitted (visible in the live feed). The original
bad reply never reaches the user.

```python
reply = self._output_guard(raw_reply, tool_names_used)
# → original reply, or "I don't have enough context..."
```

**Why a guard instead of more prompt rules?** Prompt rules are "hope the
LLM follows it". The guard is a deterministic check that runs regardless
of model quality or hallucination. It works better on weaker free-tier
models precisely because those are the ones that need it most.

### Tool-argument validation

Before any tool call, `_validate_tool_args(name, args)` checks the
arguments against the JSON schema from `tools.SCHEMAS`. It catches:
- Missing required arguments
- Wrong types (string vs integer vs boolean vs array)
- Invalid enum values

On failure, a structured error goes back to the LLM so it can correct
and retry — rather than crashing the loop or running the tool with
garbage arguments.

### Multi-provider fallback

Free tiers throttle aggressively. `_providers()` and `_cool_provider()`
in `core.py` define a chain (Groq → Gemini → OpenRouter → Cerebras)
backed by a per-provider cooldown cache. On 429 (or transient 502/503),
the current provider is benched and the next is tried. This is the right
layer to handle free-tier limits — the agent loop itself doesn't know or
care which provider answered.

`SYSTEM_PROMPT` at the top of `core.py` shapes behaviour. It's shorter
than it used to be: structural mechanisms (guard, recall tool, schema
validation) replaced several prompt rules that were just "hope the LLM
follows it."

### The HTTP protocol

We use OpenAI-compatible chat completions (Groq, OpenRouter, Gemini all
support this shape). Round 1 — we send user message + available tools:

```http
POST https://api.groq.com/openai/v1/chat/completions
{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {"role": "system",  "content": "<SYSTEM_PROMPT>"},
    {"role": "user",    "content": "what's in notes.txt?"}
  ],
  "tools": [
    {"type": "function", "function": {
      "name": "read_file",
      "description": "Read a UTF-8 text file.",
      "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
      }
    }}
  ]
}
```

Response — the LLM decides to call the tool:

```json
{"choices": [{"message": {
  "role": "assistant",
  "content": null,
  "tool_calls": [{
    "id": "call_abc",
    "function": {"name": "read_file", "arguments": "{\"path\":\"notes.txt\"}"}
  }]
}}]}
```

Round 2 — we execute, append the result with the matching `tool_call_id`,
re-send everything:

```json
{"messages": [
  {"role": "system",    "content": "<SYSTEM_PROMPT>"},
  {"role": "user",      "content": "what's in notes.txt?"},
  {"role": "assistant", "content": null, "tool_calls": [...]},
  {"role": "tool",      "tool_call_id": "call_abc",
                        "content": "today's grocery list..."}
]}
```

Round 3 — LLM produces a natural-language answer; loop exits.

### Multi-provider fallback

Free tiers throttle aggressively. `_providers()` and `_cool_provider()`
in `core.py` define a chain (`API_URL`, `API_URL_FALLBACK_1`, … through
3) backed by a 60s cooldown cache. On 429 (or transient 502/503/504,
detected by `_is_transient_provider_error`), the current provider is
benched, the next slot is tried, and we proceed. This is what lets us
run on free tiers without going dark when one provider rate-limits.

`SYSTEM_PROMPT` at the top of `core.py` shapes everything else — it's
the agent's instructions for how to use tools, when to remember things,
and how to behave on a heartbeat tick. Tweaking it is the single
highest-leverage change you can make to behavior.

### Max turns

`MAX_TURNS = 20`. Real tasks rarely need more than 5–8 tool calls. 20
caps a broken-model infinite loop from burning your rate budget.

---

## 5. Memory

The agent's long-term store is a flat directory of markdown files with
YAML frontmatter, plus a `MEMORY.md` index. **Files, not a database**,
on purpose: inspectable, version-controllable, editable by hand,
portable, no schema migrations.

```
workspace/memory/
├── MEMORY.md                          # 1-line-per-entry index
├── user_name.md
├── feedback_no_emojis.md
├── project_daily_leetcode_150.md
└── reference_obsidian_vault.md
```

Each file:

```markdown
---
name: user_name
description: The user's preferred name.
type: user
---

User goes by **Umang**.
```

### Five memory types

| type | what for | examples |
|---|---|---|
| `user` | facts about the user | name, role, timezone |
| `feedback` | corrections / preferences | "no emojis", "always ask first" |
| `project` | active work | "daily leetcode at 9am", "Tokyo trip cancelled" |
| `reference` | pointers to external resources | dashboards, vaults, repos |
| `skill` | learned multi-step procedures | "how to deliver daily leetcode" |

Why typed: it makes the index navigable and lets the agent reason about
staleness differently by type (project memories go stale fast; user
memories rarely change).

### Three-tier memory (Letta/MemGPT pattern)

Homunculus uses a three-tier approach learned from production agent
research:

**Tier 1 — Core block (always in context).**
`Memory.load_core_block()` reads the bodies of the top `user_*` and
`feedback_*` memories and injects them directly into the system prompt
at startup. These are small (capped at 300 chars each) and cover the
user's identity, timezone, and key rules. Always available, no tool call
needed.

**Tier 2 — Index (always in context, compact).**
`MEMORY.md` is one line per entry — just names and descriptions. The
agent sees the full catalog every turn and knows what to recall.

**Tier 3 — Archival (on demand via `recall()`).**
Full memory bodies are fetched only when the agent decides they're
relevant, by calling the `recall(query)` tool with specific keywords.

### Why explicit recall instead of auto-injection?

The old approach (`_inject_relevant_memory`) ran a fuzzy keyword search
on every user message and pasted the top matches into the prompt. This
caused the "Explain → example.com" bug: the query "Explain" fuzzily
matched some memory, which got injected and the agent explained *that*
instead of asking for clarification.

The fix is structural: nothing is injected automatically. The agent
decides when it needs context and calls `recall(query)`. Vague queries
produce no injection, so no confabulation.

### Memory tools

- `remember(name, description, type, body)` — write or overwrite an entry.
- `forget(identifier)` — delete an entry by name or filename.
- `recall(query)` — **the new retrieval tool**. Search memory by keywords
  and get the full bodies of matching entries.

### Compaction

The agent's running message history is capped at 15 *user turns* (not
raw messages). When it grows, oldest turns are summarised into a tight
paragraph and replaced. The count is user-turns, not raw messages,
because a tool-heavy turn (one user message → 5 tool calls → 5 tool
results) inflates raw count by 10× without adding new user context.

See `Agent._maybe_compact()` in `core.py`.

---

## 6. Tools

`tools/__init__.py` exposes two things to `core.py`:

- **`SCHEMAS`** — the live list of OpenAI-format tool definitions the LLM
  sees in every `call_llm` request. This is a proxy that re-reads from
  the MCP manager on every access so hot-reloads are picked up.
- **`execute(name, args) -> str`** — dispatch entry point. Exceptions are
  caught and stringified so a broken tool turns into information the LLM
  can recover from, not a crashed loop.

All tools are exposed via the built-in MCP server (`tools/mcp_server.py`,
runs as a subprocess over stdio). Each tool is a `@mcp.tool()` function
with typed parameters and a rich docstring — these become the schema the
LLM sees.

| category | tools | purpose |
|---|---|---|
| filesystem | `read_file`, `write_file` | local disk reads/writes |
| memory | `remember`, `forget`, `recall` | the §5 store |
| web | `web_search`, `web_fetch` | research |
| sandbox | `python`, `shell_exec` | code execution |
| scheduling | `schedule_next_tick`, `create_task`, `list_tasks`, `complete_task`, `cancel_task`, `schedule_task` | autonomy (§7) |
| notify | `notify` | Telegram push notification |

### recall() — the explicit memory retrieval tool

```
recall(query: str) -> str
```

Call this when you need facts from long-term memory. Pass keywords you
expect to appear in the memory body. Returns up to 3 matching entries
with age tags.

**Nothing is auto-injected.** The agent decides when it needs context
and asks for it. This is the key change from the old `_inject_relevant_memory()`
approach — see §5 for why.

### Adding a new tool

1. Write the implementation in `tools/<category>.py`.
2. Add a `@mcp.tool()` wrapper in `tools/mcp_server.py` with typed
   parameters and a clear docstring. The schema comes from the types and
   Field annotations — no separate JSON needed.
3. Done. The MCP manager discovers it on next server start.

The `_validate_tool_args()` function in `core.py` will automatically
enforce the schema for the new tool — required fields, type checks, enum
values — without any extra wiring.

---

## 7. Structured Tasks + Heartbeat

The heartbeat is what makes Homunculus **autonomous** rather than just
a chat bot. A daemon wakes every N minutes (or at a scheduled time)
and lets the agent do work without a user message.

```
heartbeat.py main loop:
  while True:
    tick(memory, model)        # may run reflection or due-task prompt
    sleep = compute_sleep()    # next_tick.txt or tasks.json earliest due
    time.sleep(sleep)
```

### Two scheduling mechanisms (use them right)

- **`tasks_create(title, due_at, recurrence)`** — for recurring or
  durable obligations. Lives in `workspace/tasks/tasks.json`. The
  heartbeat checks `tasks.due()` each tick; if any are due, it runs the
  agent with a prompt that includes the task. On `complete_task` the
  recurrence advances the `due_at` automatically.
- **`schedule_next_tick(iso_datetime)`** — for one-shot "wake me at X"
  timers. Lives in `workspace/_next_tick.txt`. Consumed (deleted) on
  read. Use only when the agent needs to revisit a specific moment that
  is not itself a recurring obligation.

**The wrong call here was the original leetcode bug**: the agent used
`schedule_next_tick` for "every day at 9 AM" and it failed silently
(timezone bug + no recurrence). The right call is
`tasks_create(recurrence="daily")`. Worth a `feedback_*` memory so the
agent always reaches for `tasks_create` for recurring intent.

### Reflection ticks

Once per calendar day, the heartbeat replaces the normal "any due
tasks?" prompt with a **reflection prompt**: read yesterday's log file,
identify patterns, write memory entries, optionally forget redundant
ones. This is how the agent grows without you babysitting it.

See `REFLECTION_PROMPT_TEMPLATE` and `tick()` in `heartbeat.py`. The
"is reflection due today" check uses `memory.get_last_reflection_date()`
so each calendar day reflects exactly once.

### Sleep computation

`_compute_sleep()` returns the earliest of:
- `_next_tick.txt` (if present, deleted on read)
- `tasks.next_due_seconds()` (earliest active task's `due_at`)
- `HEARTBEAT_INTERVAL_MINUTES` default (60)

So idle heartbeats sleep an hour; a task due in 10 minutes wakes the
daemon in 10 minutes.

---

## 8. Multi-channel Transports

The agent runs in *three places* today: the terminal REPL, a Telegram
bot, and a FastAPI service that backs the React SPA. Each is a thin
wrapper that calls `Agent.chat()` and persists `_session.json`.

| transport | file | what it is |
|---|---|---|
| REPL | `transports/repl.py` | `while True: input()` for development |
| Telegram | `transports/telegram.py` | long-poll bot, message in → reply out |
| Web | `transports/web_api.py` | FastAPI: `/api/*` JSON + SSE `/events` + static SPA |

All three currently share **one** `_session.json` via the workspace
volume. This causes interleaving if you chat on Telegram *and* the web
UI at the same time — a known issue that the Gateway phase (§10) will
fix by keying sessions on `(user, channel)`.

### The web frontend

`web/` is a React + TypeScript + Vite + Tailwind SPA. Vite dev server
proxies `/api` and `/events` to FastAPI for hot-reload development;
production builds bundle to static files that FastAPI serves directly
from a multi-stage Docker image.

### The event stream

Every service appends JSONL records to `workspace/_events.jsonl` via
`events.emit(event, **fields)`. The web service's `/events` SSE
endpoint (`_tail_events()` in `transports/web_api.py`) re-opens the
file every poll cycle and tracks byte offset — a long-lived file
handle's read buffer misses appends made by other processes.

On the client, `useEventStream` in `web/src/hooks/` maintains a
deduplicated shared singleton EventSource so every component (chat,
activity feed, status badges) shares one connection. Dedup is
necessary because the server re-sends the last 50 lines as initial
backfill on every reconnect (e.g. after server restarts).

---

## 9. From Hardcoded Tools to MCP Plugins

Today, tools are Python functions in `tools/`. Production agents like
OpenClaw expose each tool category as a separate **MCP server** (Model
Context Protocol — an Anthropic-led standard). The agent runtime
becomes an **MCP client**; tools become hot-pluggable processes.

Why this matters:

- **Drop-in plugins** — `git clone` an MCP repo, add to config, done.
- **Process isolation** — a buggy tool can't crash the agent.
- **Standard schema** — MCP defines tool, resource, prompt protocols.
- **Ecosystem reach** — inherit hundreds of existing MCP servers
  (GitHub, Postgres, browser, filesystem, Notion…).
- **Per-skill auth/permissions** — read-only vs full access, scoped
  by skill.

The migration is the bulk of §10's roadmap. Once `tools/__init__.py`
talks MCP, the existing `tools/*.py` files become one builtin server
(`homunculus-builtin`); external servers get added via a YAML config.

---

## 10. Test Harness

The `tests/` directory covers the deterministic parts of the agent —
the parts that don't require a live LLM or network:

```
tests/
├── test_output_guard.py      # Agent._output_guard() — all four rules
├── test_schema_validation.py # _validate_tool_args() — required/type/enum
└── test_memory.py            # Memory.search, load_core_block, CRUD
```

Run with:

```bash
uv run python -m pytest tests/ -v
```

### Why test these specifically?

These are the deterministic seams in the system — the parts where
correctness doesn't depend on an LLM making good choices:

- **Output guard**: pure function, four regex/string rules. If these
  break, bad output reaches the user. Easy to test, high value.
- **Schema validation**: pure function against a JSON schema. Protects
  against tool-arg bugs without needing a real tool call.
- **Memory**: file I/O with well-defined contracts. If search or CRUD
  breaks, the agent loses its memory silently.

The LLM behaviour itself (does it make good tool choices? does it stay
on topic?) is harder to test automatically — that's what the live
heartbeat and Telegram integration are for. But the *harness* around the
LLM can and should be tested.

### What's NOT tested here

- Live LLM calls (use the REPL or heartbeat for smoke testing)
- MCP server connections (tested by running the service)
- UI (see the web frontend — test with the Vite dev server)

The goal is a fast, zero-network suite that catches regressions on every
code change without needing API keys.

---

## 11. Roadmap

The four phases below take Homunculus from "useful personal agent"
toward production-shape (OpenClaw-style) architecture, without
breaking what already works.

### Phase A — Wrap built-in tools as an MCP server

- Stand up a single in-process MCP server that re-exports the current
  `tools/*` functions over the MCP protocol.
- Replace the direct dispatch in `core.py` with an MCP client call.
- Net effect: same behaviour, but the architecture now matches the
  industry standard. ~2–3 days of focused work.

### Phase B — Load external MCP servers

- Add `homunculus.yaml` listing additional MCP servers to launch as
  subprocesses (stdio transport).
- Wire one external server in (`@modelcontextprotocol/server-filesystem`
  or `…fetch`) to prove the loader.
- Tools available to the agent = union of all loaded MCP servers.

### Phase C — Extract a Gateway daemon

- New service: long-running WebSocket router. Transports become thin
  clients.
- Sessions key on `(user_id, channel)` inside the gateway. Telegram
  and Web no longer share `_session.json`.
- Heartbeat becomes a client of the gateway too.

### Phase D — Skill permissions, hot reload, registry

- Per-skill permission flags in config (`read_only`, `network`, `fs`).
- Watch the config; reload MCP servers on edit.
- A simple "skill marketplace" stub — a directory or URL pointing to
  known MCP servers.

---

## 11. Running Locally

### Setup

```bash
cp .env.example .env
# edit: add HOMUNCULUS_API_KEY (Groq) and optional fallbacks
# (Gemini, OpenRouter, Cerebras). Telegram and web auth optional.

uv sync                       # create venv + install deps
docker compose up -d          # heartbeat, telegram, web (+ repl on demand)
```

### Frontend dev (hot reload)

```bash
cd web && npm install         # one-time
npm run dev                   # http://localhost:5173 with HMR
```

The Vite dev server proxies `/api` and `/events` to the FastAPI
container on `:8765`, so you only rebuild Docker when you change
Python or want the bundled production SPA.

### Daily use

- **Web UI**: `http://localhost:8765` (production bundle) or
  `:5173` (HMR).
- **Telegram**: message your bot.
- **REPL**: `docker compose exec homunculus uv run python -m transports.repl`

### Free-tier notes

- **Groq** — primary, ~30 RPM free. Good for tool-use tasks.
- **Gemini Flash** — secondary, 15 RPM. Long context useful for
  reflection.
- **OpenRouter free tier** — tertiary, mix of free open-weight models.
- **Cerebras Cloud** — fast for plain inference, sparse tool support.

Multi-provider fallback (§4) cycles through these on rate-limit; one
hot provider doesn't take you down.

---

## Glossary

- **Agent loop**: while-loop that calls the LLM, executes any tools the
  LLM asks for, and re-prompts until the LLM stops calling tools.
- **Tool / skill**: a function the LLM can request you run.
  Production agents standardize on MCP servers.
- **MCP** (Model Context Protocol): Anthropic-led open standard for
  exposing tools, resources, and prompts to LLM agents.
- **Heartbeat**: background process that ticks the agent on a timer
  even with no user input, enabling autonomous work.
- **Reflection**: a once-per-day heartbeat tick that reviews yesterday
  and updates the agent's memory.
- **Memory entry**: a markdown file with YAML frontmatter, surfaced to
  the agent via `MEMORY.md`.
- **Task** (`tasks.json`): a structured durable obligation with
  recurrence; preferred over `schedule_next_tick` for any "every X"
  intent.
- **Workspace**: bind-mounted volume holding all agent state — memory,
  sessions, events, tasks, chapter archives. Inspectable from your
  host editor.
- **Event log** (`_events.jsonl`): single source of truth for what
  happened. Powers the live UI; survives service restarts.
- **Provider fallback**: a chain of LLM endpoints tried in order;
  rate-limited providers go into a 60s cooldown.
- **Chapter**: an explicitly-closed conversation, archived to
  `workspace/_chapters/`. Fresh start without losing memory.
- **Gateway** (planned): central WebSocket router that owns sessions
  and forwards messages between transports and the agent runtime.
