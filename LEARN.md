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

`Agent.chat()` in `core.py` is the whole loop. The streaming variant
is `Agent.chat_stream()` (same control flow, yields deltas instead of
returning a final string). Stripped to its essence:

```python
def chat(self, user_text: str) -> str:
    self.history.append({"role": "user", "content": user_text})
    for _ in range(MAX_TURNS):
        msg = call_llm(self.history, tools.SCHEMAS, model=self.model)
        self.history.append(msg)
        if not msg.get("tool_calls"):
            return msg["content"]
        for call in msg["tool_calls"]:
            result = tools.execute(call["function"]["name"],
                                   json.loads(call["function"]["arguments"]))
            self.history.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })
    return "[hit max turns]"
```

That's it. The streaming variant `chat_stream()` yields content/tool
deltas as the LLM produces them, but the control flow is identical.

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

### Four memory types

| type | what for | examples |
|---|---|---|
| `user` | facts about the user | name, role, timezone |
| `feedback` | corrections / preferences | "no emojis", "always ask first" |
| `project` | active work | "daily leetcode at 9am", "Tokyo trip cancelled" |
| `reference` | pointers to external resources | dashboards, vaults, repos |

Why typed: it makes the index navigable and lets the agent prioritize
when context is tight ("recall feedback before answering").

### Memory tools

- `remember(name, description, type, body)` — write or overwrite an entry.
- `forget(identifier)` — delete an entry by name or filename.
- `search_memory(query)` — substring + keyword match across all entries.

`MEMORY.md` is auto-regenerated by `_upsert_index_entry()` whenever an
entry is added/edited/forgotten. It's the only file the *system prompt*
always includes — concise summaries are how the agent stays
context-aware without us paying for the full vault each turn.

### Compaction

The agent's running message history (`workspace/_session.json`) is
capped at ~15 turns. When it grows, oldest turns are summarised into a
project memory and dropped from the rolling window. The relationship
persists in memory; the conversation buffer stays cheap.

See `_compact_history()` in `core.py`. The summarisation prompt is
literal — read it before tuning compaction.

---

## 6. Tools

`tools/__init__.py` exposes two things to `core.py`:

- **`SCHEMAS`** — the list of OpenAI-format tool definitions the LLM
  sees in every `call_llm` request.
- **`execute(name, args) -> str`** — single dispatch entry point. All
  exceptions are caught and stringified so a broken tool turns into
  information the LLM can recover from, not a crashed loop.

Each tool category lives in its own file under `tools/`:

| file | tools | purpose |
|---|---|---|
| `filesystem.py` | read_file, write_file, list_files | local disk |
| `memory_tools.py` | remember, forget, search_memory | the §5 store |
| `web.py` | web_search, web_fetch | research |
| `sandbox.py` | python, shell_exec | code execution |
| `scheduling.py` | schedule_next_tick, tasks_create, tasks_list, complete_task, cancel_task | autonomy (§7) |
| `notify.py` | notify | sends a message to the user's Telegram |

### Two structures bridge LLM-world and Python-world

- `SCHEMAS` (LLM-facing): JSON schema list. Names + descriptions +
  parameter types.
- `TOOLS` (Python-facing): `dict[name → callable]`.

Names in `SCHEMAS` must match keys in `TOOLS` exactly. Schema parameter
names must match Python keyword arguments exactly. This is the only
brittle invariant in the system — if you rename a tool, edit both.

### Adding a new tool

1. Write the Python function in the appropriate `tools/*.py` file.
2. Append the matching JSON schema to that file's `SCHEMAS_LOCAL`.
3. Register both in `tools/__init__.py` via the per-file registry.
4. Update the system prompt only if the tool's purpose isn't obvious
   from its description.

That's it — the loop in `core.py` doesn't need to change.

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

## 10. Roadmap

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
