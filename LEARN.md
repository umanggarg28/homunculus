# LEARN.md — Homunculus

> Currently covers: **Phase 1** (agent core), **Phase 2** (persistent memory),
> **Phase 2.5** (daily logs + age awareness). Phases 3 (heartbeat) and 4
> (messaging) are still ahead.

A reference doc for understanding every piece of this project. Written to be
useful months from now when you've forgotten the details. If you only read
one section, read **The Mental Model**.

---

## What is Homunculus?

A minimal autonomous personal AI assistant, built from scratch, no
frameworks. The eventual goal (Phases 2–4) is an assistant that lives in a
container, remembers things between sessions, wakes up on its own every few
minutes to check in on tasks, and can be talked to from a messaging app.

**Phase 1** (this milestone) covers the foundation: the agent loop, three
tools, a REPL, and a Dockerized run environment. About 250 lines of Python
across three files.

---

## The Mental Model

**An agent is a while-loop wrapped around an LLM API call, where the LLM is
allowed to request that you run functions on its behalf.**

That's it. No magic. The "intelligence" lives entirely in the LLM. Our job
is to write the loop and the functions.

Three things follow from this:

1. **The LLM never executes anything itself.** It can only emit text —
   including text that says "please run this function with these args." We
   are the hands; the LLM is the brain.
2. **The loop terminates when the LLM returns text without a tool call.**
   That's the entire termination condition.
3. **We accumulate the conversation history.** The LLM is stateless. Every
   request resends the whole history. We are the memory.

Once those three ideas click, every "agent framework" you'll ever see is
elaboration on this 30-line loop.

---

## The Tool-Use Protocol (HTTP level)

We use Groq's chat completions endpoint, which is OpenAI-compatible. Here's
what the wire looks like for the message `"what's in notes.txt?"`.

### Round 1: we send the user message + available tools

```http
POST https://api.groq.com/openai/v1/chat/completions
Authorization: Bearer <HOMUNCULUS_API_KEY>
Content-Type: application/json

{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {"role": "system", "content": "You are Homunculus..."},
    {"role": "user", "content": "what's in notes.txt?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from disk.",
        "parameters": {
          "type": "object",
          "properties": {"path": {"type": "string"}},
          "required": ["path"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

### Round 1 response: LLM asks us to call a tool

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "read_file",
          "arguments": "{\"path\": \"notes.txt\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

Note: `content` is null and `tool_calls` is populated. The `arguments`
field is a **JSON-encoded string**, not a real JSON object — we have to
parse it with `json.loads()`.

### Round 2: we send the original messages + the assistant's tool-call message + our tool's result

```json
{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "what's in notes.txt?"},
    {"role": "assistant", "content": null, "tool_calls": [...same as above...]},
    {"role": "tool", "tool_call_id": "call_abc123", "content": "buy milk"}
  ],
  "tools": [...same...],
  "tool_choice": "auto"
}
```

The `tool` role message references the `tool_call_id` from the assistant's
call. This is how the LLM matches results to requests (important when it
makes multiple parallel calls).

### Round 2 response: final answer

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "notes.txt contains a single line: 'buy milk'.",
      "tool_calls": null
    },
    "finish_reason": "stop"
  }]
}
```

`tool_calls` is now null and `content` is a real string. **That's our signal
to exit the loop and show the answer to the user.**

---

## File Reference

### `core.py` — the agent loop

The heart of the project. Contains:

- **`call_llm(messages, tool_schemas)`** — one POST to Groq, returns the
  assistant message dict. Raw httpx, no SDK.
- **`Agent`** class — holds message history; method `chat(text)` runs the
  tool-use loop until the LLM returns a real answer.
- **`SYSTEM_PROMPT`** — sets the agent's role and behavior.
- **`MAX_TURNS = 20`** — hard cap to prevent infinite tool-call loops.

**Key data flow in `Agent.chat()`:**

```
user message → append to history
loop up to MAX_TURNS:
    LLM response ← call_llm(history, schemas)
    append response to history
    if no tool_calls: return response.content
    for each tool_call:
        result = tools.execute(name, args)
        append {role: tool, tool_call_id, content: result} to history
```

The whole loop is ~30 lines. Re-read it occasionally — it's the project.

### `tools.py` — capabilities + schemas

Three tools, each with a Python function and a matching JSON schema:

- **`read_file(path)`** — reads UTF-8 text, returns string.
- **`write_file(path, content)`** — writes text, creates parent dirs.
- **`shell_exec(command)`** — runs shell command with user `y/N`
  confirmation, 60s timeout, output truncated at 4000 chars.

Two structures bridge LLM-world and Python-world:

- **`SCHEMAS`** — list of OpenAI-format tool definitions; what the LLM sees.
- **`TOOLS`** — dict mapping name → Python function; how we dispatch a call.

The names in `SCHEMAS` and the keys in `TOOLS` must match exactly. The
function signatures must match the schema's parameter names exactly.

**`execute(name, args)`** is the single entry point for the loop. It catches
all exceptions and returns them as strings so a tool error doesn't crash
the agent — it becomes information the LLM can reason about.

### `main.py` — REPL entry point

Loads `.env`, instantiates `Agent`, runs a `while True: input()` loop.
Special commands: `exit`, `quit`, `reset`. The frontend handles its own
controls; only real user messages get forwarded to `agent.chat()`.

### `pyproject.toml` + `uv`

We use uv (Rust-based, fast) instead of pip. Deps are declared in
`pyproject.toml`. Locked exact versions are in `uv.lock` (committed).

- `uv sync` — create `.venv/` and install deps
- `uv run python main.py` — run a command with the venv active
- `uv add <pkg>` — add a dependency (updates both files)

### `Dockerfile` + `docker-compose.yml`

The container is the sandbox. `shell_exec` runs inside it — limited blast
radius if the LLM does something dumb.

Compose flags that matter:
- `stdin_open: true` + `tty: true` — required for the interactive REPL.
- `env_file: .env` — injects `HOMUNCULUS_API_KEY` into the container.
- `volumes: - ./workspace:/app/workspace` — bind-mounts a host directory so
  files the agent creates survive container exit and are visible to your
  editor.
- `working_dir: /app/workspace` — agent's relative paths resolve there.

---

## Design Decisions

### Why no SDK / framework?

The educational value of seeing the raw HTTP request is the point of this
project. SDKs (Anthropic, OpenAI) and frameworks (LangChain, CrewAI,
LangGraph) hide exactly the parts that are most worth understanding: the
message shape, the tool-call protocol, the loop. Once you understand those
~150 lines, you understand every agent framework that ships next year too.

### Why Groq + Llama 3.3?

- **Free tier.** Sufficient for development.
- **Fast.** Lower latency than most providers — the REPL feels snappy.
- **OpenAI-compatible API.** Same JSON shapes as OpenAI, so most reference
  code translates verbatim.
- **Tool-use capable.** Not all models do tool-use well; Llama 3.3 70B is
  near the top of the free-tier options.

### Why `MAX_TURNS = 20`?

A real task rarely needs more than 5 tool calls. 20 is a generous safety
net for chained operations (e.g., "audit the project: list files, read
each one, summarize"). Tight enough that a broken model can't burn through
your rate limit in one session.

### Why a class for `Agent` instead of just functions?

State. The message history accumulates across `chat()` calls. A class is
the most idiomatic Python home for stateful behavior. Could've used a
closure or a global; class is cleaner.

### Why `shell_exec` requires confirmation?

Phase 1 runs the container with a TTY so we can prompt the user before
each shell command. This is paranoid — even in the container — because the
container's filesystem and network are still real, and we're learning.
Phase 3+ (background daemon) will pre-approve some commands via an
allowlist.

### Why bind-mount `workspace/` instead of running in a totally clean container?

So files the agent creates are visible in your host editor in real time.
You can run `homunculus` in one terminal and have VS Code open on
`workspace/` watching files appear. This is incredibly satisfying and also
the fastest way to debug what the agent is doing.

---

## How to Run

### One-time host setup

Install uv (if you don't have it):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Get a Groq API key from https://console.groq.com/keys (free).

```bash
cd homunculus/
cp .env.example .env
# edit .env, paste your key
```

### Local (no Docker)

```bash
uv sync               # one-time: creates .venv and installs deps
uv run python main.py # start the REPL
```

### In Docker (recommended once you're past initial tinkering)

```bash
docker compose run --rm homunculus
```

First run builds the image (~30s). Subsequent runs are instant.

### Try these prompts

- `make a file called hello.txt with the text "hi from homunculus"`
- `what's in this directory?`
- `read hello.txt and tell me how many characters it has`
- `make a python script that prints fibonacci numbers and run it`

Watch the `-> tool_name(args)` lines — that's the agent thinking out loud.

---

## How to Extend (Adding a Tool)

Say you want a `http_get(url)` tool. Three changes:

**1. Write the function in `tools.py`:**

```python
def http_get(url: str) -> str:
    response = httpx.get(url, timeout=10.0, follow_redirects=True)
    return response.text[:4000]
```

**2. Add the schema to `SCHEMAS`:**

```python
{
    "type": "function",
    "function": {
        "name": "http_get",
        "description": "GET a URL; returns response body (first 4000 chars).",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
}
```

**3. Register in `TOOLS`:**

```python
TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "shell_exec": shell_exec,
    "http_get": http_get,        # new
}
```

**4. Mention it in the system prompt** (`core.py:SYSTEM_PROMPT`) so the LLM
knows it exists in a high-level sense, not just via the schema.

Done. The agent loop needed zero changes.

---

## Common Pitfalls

- **Forgetting to `json.loads(arguments)`.** The `arguments` field comes
  back as a JSON string, not a dict. Calling `**arguments` directly crashes.
- **Mismatched tool name in `SCHEMAS` vs `TOOLS`.** The LLM calls by name;
  `TOOLS[name]` does the lookup. If they don't match, you'll see
  `"unknown tool"` errors that look like LLM errors but are config bugs.
- **Forgetting `tool_call_id`.** Every `{role: "tool"}` message must
  reference the `id` from the assistant's tool_call. Otherwise the API
  rejects the request with a confusing error.
- **Putting the tool result *before* the assistant tool-call message.** The
  history order is: assistant(tool_call) → tool(result). Reverse it and the
  API refuses.
- **Not setting `timeout` on httpx.** A hung LLM request will block
  forever. We use 60s.
- **`shell=True` without sandboxing.** Outside a container, this is a
  security disaster. Inside a container with a confirmation prompt, fine.
- **Hitting `MAX_TURNS`.** Usually means the LLM is stuck in a tool-call
  loop. Look at the printed `-> tool(...)` traces to see what it's doing.

---

## Phase 2 — Persistent Memory

Memory turns Homunculus from a chatbot that forgets everything into an
assistant that remembers facts across sessions. The design: typed
markdown files with YAML frontmatter, plus an index file that gets
injected into the system prompt every session.

### What was added

- **`memory.py`** — new module with a `Memory` class that knows how to
  read/write memory files on disk.
- **`remember(name, description, type, body)` tool** — registered in
  `tools.py`, lets the LLM save durable facts via the same tool-call
  mechanism it already uses for `read_file` etc.
- **`Agent.reflect()`** — a new method that asks the LLM to look back
  on the conversation and decide what's worth keeping. Called from
  `main.py` after the user types `exit`.
- **System prompt expanded** — tells the LLM about the four memory
  types (user/feedback/project/reference) and the index/detail-files
  split.

### Memory directory layout

```
workspace/memory/
    MEMORY.md                # index — loaded into every system prompt
    user_<slug>.md           # facts about the user
    feedback_<slug>.md       # collaboration rules
    project_<slug>.md        # ongoing work context
    reference_<slug>.md      # pointers to external resources
```

Each entry file has frontmatter:

```yaml
---
name: <title>
description: <one-line summary>
type: user|feedback|project|reference
---

<body>
```

`MEMORY.md` is a one-line-per-entry index. Bodies are read on demand
via the existing `read_file` tool — so the prompt stays small even as
memory grows.

### The four memory types

| Type        | What goes there                                         |
|-------------|----------------------------------------------------------|
| `user`      | Role, expertise, preferences                            |
| `feedback`  | Collaboration rules ("don't do X", "always do Y")       |
| `project`   | Ongoing work context (decays over time)                 |
| `reference` | Pointers to external resources (URLs, doc locations)    |

The split matters because different types decay differently — feedback
rules are durable, project facts go stale, references may rot when
external URLs change.

### The dependency injection trick in `tools.py`

The `remember` tool needs a `Memory` instance to call into. The other
tools don't. Rather than threading memory through every tool call, we
gave `tools.py` a private variable `_memory` that starts empty, plus
a setter:

```python
_memory: Memory | None = None

def init(memory: Memory) -> None:
    global _memory
    _memory = memory
```

`main.py` calls `tools.init(memory)` once at startup. Because Python
only imports each module once, that single assignment is visible to
every later call of `remember()`. The leading underscore is a
convention meaning "private to this file."

### End-of-session reflection

When the user types `exit`, `main.py` calls `agent.reflect()`:

```python
def reflect(self) -> str:
    return self.chat(
        "We're ending this session. Reflect on our conversation: ..."
    )
```

This is just a regular `chat()` call with a special prompt. The LLM
looks back, decides what's worth saving, and calls `remember()` for
each fact — same tool it uses mid-conversation. Nothing magic here.

### Why markdown and not SQLite?

We chose markdown because:
1. The LLM can use its existing `read_file` / `write_file` tools to
   interact with memory. No new SQL tool needed.
2. You can open it in an editor and read it.
3. Git-friendly diffs.

LangGraph and similar frameworks use SQLite — but they're solving a
different problem (full session checkpointing, not curated semantic
memory). Different shape entirely.

---

## Phase 2.5 — Daily Logs & Age Awareness (KAIROS)

Phase 2 gave us *curated* memory. Phase 2.5 adds *raw* memory: every
turn is also appended to a daily log file, organized hierarchically
by year/month. We call this layer KAIROS.

### What was added

- **`Memory.log_turn(role, content)`** — appends a timestamped entry
  to `memory/logs/YYYY/MM/YYYY-MM-DD.md`. Append-only, never modified.
- **`Memory.recent_log_paths(days=3)`** — returns log file paths from
  the last N days (newest first). Used by reflection.
- **`Memory.load_index()` enhanced** — now injects age annotations at
  read time. Each entry in the index shows up with "(today)" /
  "(yesterday)" / "(3 days ago)" / "(~2 weeks ago)" etc.
- **Staleness markers** — entries older than 30 days get a ⚠ flag.
- **System prompt updated** — tells the LLM that logs exist and may
  be reviewed during reflection.

### Why two layers (curated + raw)?

Two completely different jobs:

| Layer           | Purpose                                  | Lifecycle             |
|-----------------|------------------------------------------|------------------------|
| Typed memory    | "What do I durably know about the user?" | LLM-curated, upserted |
| Daily logs      | "What literally happened on date X?"     | Append-only, immutable|

Raw logs preserve fidelity. Typed memory is the distilled signal. The
reflection step at session-end is the distillation pass — read recent
logs if needed, then save typed entries.

### Why human-language ages, not timestamps?

LLMs are bad at date arithmetic. Given a timestamp `2026-04-22T14:33:01`,
they often can't tell you how stale it is. Given "47 days ago," they
will. So we humanize:

```python
def _humanize_age(mtime: float) -> str:
    days = int((time.time() - mtime) // 86400)
    if days <= 0: return "today"
    if days == 1: return "yesterday"
    if days < 14: return f"{days} days ago"
    if days < 60: return f"~{days // 7} weeks ago"
    if days < 365: return f"~{days // 30} months ago"
    ...
```

The principle: LLMs reliably parse plain-language ages but struggle with
raw timestamps. A model that can't tell you how stale `2026-04-22T14:33`
is will instantly recognize that "47 days ago" is old enough to verify.

### Ages are injected, not stored

`MEMORY.md` on disk has no age info — just the link and description.
When `load_index()` is called, it reads the file's mtime for each
entry and prepends an age annotation in the returned string. So:

- On disk:
  `- [favorite_color](./user_favorite_color.md) — terracotta`
- What the LLM sees in its prompt:
  `- [favorite_color](./user_favorite_color.md) *(yesterday)* — terracotta`

Why not just write the age into the file? Because it'd be stale
instantly. Generating at read-time keeps it always current.

### File layout after Phase 2.5

```
workspace/memory/
    MEMORY.md
    user_*.md, feedback_*.md, project_*.md, reference_*.md
    logs/
        2026/
            05/
                2026-05-17.md
                2026-05-18.md
            06/
                2026-06-01.md
```

---

## What's Next

Phase 2 is complete. The next milestones:

### Phase 3 — Heartbeat daemon
An async background process that wakes every N minutes and self-prompts
the agent: "Given recent memory, is there anything proactive you should
do?" This is when it stops being a chatbot and becomes a real *agent*.

### Phase 4 — Messaging bridge
A Telegram (or Discord) bot bridging your phone ↔ the agent. The
heartbeat can push proactive messages.

### See also: `IDEAS.md`
Deferred improvements (LLM-based memory retrieval, embedding search,
session resume, mid-session compaction, etc.) are listed in `IDEAS.md`.
Each entry says what it is, why we deferred it, and when revisiting
would be worthwhile.

---

## Glossary

- **Agent.** A loop around an LLM call that lets the LLM call functions
  ("tools") and feed their results back into its next decision.
- **Tool.** A (function, JSON-schema) pair the agent can invoke.
- **System prompt.** The first message in a conversation, with role
  `"system"`. Tells the LLM who it is and how to behave.
- **Tool call / tool_call_id.** When the LLM wants to invoke a function, it
  emits a `tool_calls` array with one entry per call, each with a unique
  `id`. Our reply uses that `id` to attach the result.
- **MAX_TURNS.** Hard cap on tool-call iterations per user message. Safety
  net against runaway loops.
- **uv.** A fast Python package manager / venv manager from Astral. Used
  here instead of pip.
- **Bind mount.** A Docker volume that maps a host directory to a container
  directory. Files are shared bidirectionally in real time.
- **Container sandbox.** Running the agent inside Docker so `shell_exec`'s
  blast radius is the container's filesystem, not your host.
