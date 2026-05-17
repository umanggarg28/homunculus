# LEARN.md — Homunculus

> Covers: **Phase 1** (agent core), **Phase 2** (persistent memory),
> **Phase 2.5** (daily logs + age awareness), **Phase 3** (autonomous
> heartbeat), **Phase 4** (Telegram bridge), **Phase 5.0** (token
> efficiency: compaction, model override, index cap), **Phase 5.1**
> (self-scheduling heartbeat), **Phase 5.2** (web research), **Phase
> 5.3** (sandboxed code execution).

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

## Phase 3 — Autonomous Heartbeat

This is the phase that turns Homunculus from a chatbot into a real
agent. A background daemon wakes every N minutes and self-prompts:
"Look at your memory and recent logs. Is there anything proactive
worth doing right now?" The agent decides for itself. Nothing else has
to be running — no REPL open, no human at the keyboard.

### What was added

- **`heartbeat.py`** — new module. An infinite loop that builds a fresh
  `Agent`, runs ONE conversation turn with the heartbeat prompt, sleeps
  N minutes, repeats. Catches exceptions per-tick so a single bad tick
  can't kill the daemon.
- **`tools.py` mode flag** — `init(memory, autonomous=True)` puts tools
  into autonomous mode. In that mode `shell_exec` refuses to run and
  tells the LLM to leave a note instead.
- **A second service in `docker-compose.yml`** — `heartbeat` shares the
  same image and the same workspace volume as the REPL, but runs
  `heartbeat.py` instead of `main.py`. No stdin/tty (it's a daemon).
- **`HEARTBEAT_INTERVAL_MINUTES`** env var — default 10.

### How to run

```bash
# Talk to it (interactive, when you want)
docker compose run --rm homunculus

# Start the autonomy daemon (background, auto-restart)
docker compose up -d heartbeat
docker compose logs -f heartbeat     # watch what it does
docker compose stop heartbeat        # stop it
```

You can run both at the same time. They share the memory folder, so
anything the heartbeat learns shows up in your next REPL session, and
vice versa.

### Safety: why shell_exec is disabled in autonomous mode

In REPL mode, `shell_exec` shows the command and asks for y/N. There's
a human in the loop. In heartbeat mode there is no human, no terminal,
no way to ask. Three options for what to do about shell access:

1. Auto-allow everything → too risky even inside a container
2. Allowlist specific commands → real engineering, defer to later
3. Disable entirely, tell the LLM to leave a note → cheap, safe, gives
   the LLM a sensible fallback

We picked (3). When the autonomous agent decides it needs to run a
shell command, `shell_exec` returns:

> "BLOCKED: shell_exec is disabled in autonomous (heartbeat) mode. If
> you really need this command run, call remember() to leave a note
> for the user describing what you want and why; they'll execute it
> next REPL session."

So the agent's recourse is to write a memory entry like "User: please
run `pytest tests/` to check if my changes pass." Next time you start
the REPL, you see that note in the memory index and decide.

### Prompt engineering: "don't invent work"

The biggest failure mode of a self-prompted LLM is inventing tasks just
to feel productive. ("Let me reorganize the memory folder…") We
explicitly tell it that doing nothing is fine:

> "If nothing genuinely useful comes to mind, say so in one line and
> STOP. Do NOT invent work just to feel productive. Doing nothing is
> fine."

In practice this works — in our smoke test, the heartbeat read today's
log file, decided there was nothing to do, and exited the tick. That's
the desired behavior.

### Why a fresh Agent every tick (not one persistent agent)?

Each heartbeat tick creates a new `Agent(memory=memory)`. We don't
carry conversation history across ticks. Three reasons:

1. **Bounded context.** A persistent agent would accumulate hours of
   tick history. The fresh-agent design keeps each tick small.
2. **Independence.** Each tick is its own reasoning, not influenced by
   what the agent decided 5 ticks ago.
3. **Continuity comes from memory, not history.** The agent doesn't
   need to remember "5 minutes ago I decided X" — if X was important,
   it should already be in the memory index.

This is a deliberate architectural choice: ticks share *memory* (long-
term, curated) but not *history* (short-term, conversational).

### Operational lessons (from first runs)

Four issues surfaced during the first autonomous demo and got fixed.
Recording them here because the underlying causes apply broadly to
any agent system, not just this one.

**1. LLMs ignore path conventions, no matter how clearly stated.**
The system prompt told the agent its cwd was `workspace/`. It still
wrote files to `workspace/summary.md`, producing a nested
`workspace/workspace/`. Fix: defensive path normalization in
`read_file` / `write_file` — strip `workspace/` and `/app/workspace/`
prefixes server-side. The agent can be wrong about cwd and still land
in the right place. **General lesson: don't trust prompt instructions
for correctness; treat them as preferences and enforce constraints in
code.**

**2. Rate limits hit fast with verbose prompts.**
Each tick was burning ~5K tokens (system prompt + memory index +
log content). Free tier is 8K TPM. Back-to-back ticks within a minute
exceed it. Fix: parse the `retry-after` header on 429, sleep that
long + 1s buffer, retry once. **General lesson: any HTTP integration
with a rate-limited API needs retry-with-backoff. Don't assume
unlimited quota.**

**3. Self-reading feedback loops.**
The heartbeat prompt suggested reading today's log file. The agent
took the suggestion every tick. The log contains the agent's own
*previous* heartbeat output. Each tick read the bloated log, added
more text to the log, and the cycle compounded. Fix: explicit prompt
instruction "DO NOT read the daily log files unless you have a
specific recall task". Also capped `read_file` output at 16KB (tail
preserved). **General lesson: when an agent both reads and writes the
same source, the system has positive feedback. Either break the
loop in the prompt or bound the read.**

**4. Per-tick error isolation matters.**
The retry failed the first time. Without the `try/except` in
`heartbeat.tick()`, that single error would have killed the daemon.
Instead the daemon logged and kept ticking. **General lesson: a
background daemon must catch and continue on transient errors.
Crash-on-first-failure is fine for CLI tools, not for services.**

### File layout after Phase 3

```
homunculus/
    core.py          tools.py        memory.py
    main.py          ← REPL entry
    heartbeat.py     ← daemon entry (NEW in Phase 3)
    Dockerfile       docker-compose.yml
    workspace/
        memory/  …   ← shared between REPL and heartbeat
```

---

## Phase 4 — Telegram Bridge

The third way to talk to Homunculus: from your phone. A Telegram bot
runs as its own Docker service, receives messages, routes them to a
persistent `Agent`, and sends replies back. Also gives the heartbeat
a new `notify()` tool so it can push proactive messages to your phone
when it has something worth interrupting you about.

### What was added

- **`telegram_bot.py`** — new daemon. Long-polls Telegram for messages,
  routes them through the agent loop, sends replies back. Single-user
  locked (only your `TELEGRAM_ALLOWED_USER_ID` can talk to it).
- **`notify(text)` tool** in `tools.py` — sends a Telegram message via
  the bot token + chat ID. Works from any service (REPL, heartbeat,
  even the bot itself) as long as Telegram env vars are configured.
- **Third Docker service** in `docker-compose.yml` — `telegram`. Shares
  the same image and the same workspace volume as REPL and heartbeat.
- **Self-onboarding** — if `TELEGRAM_ALLOWED_USER_ID` is unset, the bot
  greets any incoming message with the sender's user ID and tells you
  what to paste into `.env`. No need for a separate ID-fetcher bot.
- **`python-telegram-bot`** added to `pyproject.toml`.

### How to set it up

1. Telegram → search `@BotFather` → `/newbot` → get a token.
2. Put it in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_ALLOWED_USER_ID=
   ```
   (leave the user ID blank initially)
3. `docker compose up -d telegram`
4. Open your bot in Telegram and send any message.
5. The bot replies with your numeric user ID. Paste it into
   `TELEGRAM_ALLOWED_USER_ID=...` and `docker compose restart telegram`.
6. Now talk to your bot — it'll respond as the agent.

### Single-user lock

The bot rejects any message whose `effective_user.id` doesn't match
`TELEGRAM_ALLOWED_USER_ID`. This matters: a Telegram bot is *publicly
discoverable* — anyone who finds your bot's username can message it.
Without the lock, randoms could trigger your agent (and burn your
Groq quota, write files in your workspace, etc.).

### Architecture: three services, one memory

```
Telegram app    ↓ messages           ↑ replies + notifications
                ↓                    ↑
       [telegram service]
                ↓ Agent.chat()       ↑
                ↓                    ↑
       memory/ + workspace/ ←──┐
                ↑              │ shared volume
       [heartbeat service]     │
                ↓ Agent.chat() │
                ↓              │
                └──── ticks ───┘

       [homunculus REPL]
       (run on demand)
                ↑ Agent.chat()
                └──── REPL ────┘
```

Three separate Agent instances (one per service), but they share the
same `memory/` directory. Anything any of them remembers, the others
see in their next session.

### Plain-text formatting hack

Telegram doesn't render markdown tables. It also crashes on `parse_mode`
errors if the agent emits unbalanced asterisks or special chars. So we
take two precautions:

1. **Prompt suffix**: `TELEGRAM_PROMPT_SUFFIX` added to the agent's
   system prompt tells the LLM to write in plain-text style — bullets
   with `-`, no tables, no headers, no `**bold**`.
2. **Post-processor**: `_clean_for_plaintext()` strips `**`, `__`,
   `` ` ``, `# ` prefixes, and converts any leftover table rows into
   `cell · cell · cell` lines before sending.

The post-processor is what actually works. LLMs ignore prompt-level
formatting instructions about as reliably as they ignore path
conventions — defensive code in the bot is what saves us. Same pattern
as Phase 3's `_normalize_workspace_path()`.

### Why `autonomous=True` in the bot too

The bot calls `tools.init(memory, autonomous=True)` — same flag as
the heartbeat. Why disable `shell_exec` in an interactive bot? Because
**there's no terminal attached to the bot process.** `shell_exec`'s
y/N prompt uses `input()`, which would block the asyncio event loop
forever. Better to refuse and tell the agent to use `remember()` to
ask for shell help via the user's next REPL session.

If you want shell access through Telegram in the future, the right
design is a callback-button approval flow: agent calls `shell_exec`,
bot sends "Approve `ls -R` ?" with inline buttons, user taps Yes/No,
bot runs (or doesn't) and replies. Real engineering — saved for later
in `IDEAS.md`.

---

## Phase 5.0 — Token-efficiency Package

The free Groq tier has an 8000-tokens-per-minute ceiling. We hit it
during the first heartbeat demos. Three optimizations land in 5.0 to
bound token usage before Phase 5.1+ feature work makes prompts bigger.

### 5.0a — Mid-session compaction

When `Agent.history` grows past `COMPACT_TRIGGER` (30 messages), the
older portion is replaced with a one-paragraph summary written by the
LLM itself.

- Cut point: at a user-message boundary (`COMPACT_KEEP_RECENT=12`
  recent user turns kept verbatim). Never split a paired
  `(assistant_with_tool_calls → tool_result)` sequence.
- Summary call: uses `call_llm(messages, tool_schemas=None)` —
  no tools, plain completion, much cheaper.
- Where it runs: `Agent._maybe_compact()` is called at the top of
  every `chat()` so growth is bounded before each new turn.

### 5.0b — Per-service model override

`Agent(..., model="...")` lets each service pick its own model. The
heartbeat (default `openai/gpt-oss-20b`) burns ~6x fewer tokens per
tick than the 120B used for REPL/Telegram, and the heartbeat's task
doesn't need the bigger model's reasoning quality.

Override via `HOMUNCULUS_MODEL_HEARTBEAT` env var if you want.

### 5.0c — Memory index cap

`Memory.load_index(max_entries=30)` sorts entries by mtime newest-first
and caps the result. Older memories stay on disk and are still
discoverable — they just don't auto-appear in every system prompt. A
footer line tells the LLM how many were hidden:

> `(Showing the 30 most recently-touched memories out of 47. Older
> entries remain on disk — use read_file to fetch them if you remember
> their filename.)`

Why a simple time-based cap instead of embedding-based retrieval? It
covers 90% of the value at 5% of the complexity. Embedding search
goes in `IDEAS.md` for when we actually have >100 memories.

---

## Phase 5.1 — Self-scheduling Heartbeat

The heartbeat used to sleep for a fixed `HEARTBEAT_INTERVAL_MINUTES`.
Now the agent itself decides when to wake next — by calling a new
tool, `schedule_next_tick(iso_datetime)`. If it doesn't call the tool,
the daemon falls back to the configured interval.

### Flow

1. Heartbeat tick runs. The prompt includes `current_time = ...` so
   the agent can compute relative times like "8am tomorrow."
2. The agent may call `schedule_next_tick("2026-05-18T08:00:00")`.
   The tool validates (future, within 24h, parseable) and writes the
   target to `memory/_next_tick.txt`.
3. After the tick, the daemon calls `memory.pop_next_tick()` — reads
   the value AND deletes the file (so a missed schedule next time
   doesn't reuse the stale value).
4. Daemon sleeps until the target time (or default interval if none).

### Why it matters

Without this, the agent's autonomy felt mechanical — every N minutes,
on the clock. With it, the agent can have **temporal intent**:

> "Tokyo flight booking deadline is tomorrow at 5pm. Set next tick to
> tomorrow at 9am so I can nudge the user one final time before the
> deadline."

That's the qualitative difference between "scheduled cron" and "agent
that knows when to act."

### Safety belts

- Tool rejects past times → `"in the past"` error
- Tool rejects >24h schedules → can't disappear for weeks
- Tool validates ISO format → no silent parse failures
- Daemon double-checks at sleep time (defense in depth)

---

## Phase 5.2 — Web Research

Two new tools — the agent can now actually research things.

### `web_search(query)`

Wraps Tavily (https://tavily.com, 1000 free searches/month). Returns:
- An auto-generated answer summary (when Tavily judges it useful)
- Up to 5 results: title, URL, content snippet

Why Tavily over Serper: Tavily is built for agents. Its snippets are
content-extracted, not just metadata, so a single search often
finishes the research. Serper-style Google results would require a
follow-up `web_fetch` for almost every query, doubling API roundtrips.
The dispatcher is provider-agnostic (`WEB_SEARCH_PROVIDER` env var),
so we can swap later.

### `web_fetch(url)`

Downloads a page and returns its main text content. Uses
BeautifulSoup to strip noise (script, style, nav, footer, aside,
header, noscript), then collapses blank lines. Capped at
`READ_FILE_MAX_CHARS` (16K) — the LLM rarely needs more, and bigger
blobs blow up the context window.

### Why both tools, not just one

Tavily snippets are good but truncated (~500 chars each). For a deep
read — full article, documentation page — the agent needs `web_fetch`.
The natural workflow:

1. `web_search("groq pricing 2026")` → 5 snippets with URLs
2. Agent picks the most authoritative URL
3. `web_fetch(url)` → full page text
4. Agent cites it in the response

### Setup

Sign up for a Tavily key (free), put it in `.env`:

```
TAVILY_API_KEY=tvly-...
```

If unset, `web_search` returns a clear "key not configured" message —
the agent learns the tool isn't available this session and stops
trying it.

---

## Phase 5.3 — Sandboxed Code Execution

A `python_exec(code)` tool that runs Python in a **fresh, isolated
container** per call. The agent can compute, parse, verify snippets,
explore data — without any of that code being able to touch our
workspace, memory, or the network.

### Sandbox shape

Each call spawns a sibling container with these flags:

| Flag | What it does |
|---|---|
| `--rm` | container auto-deleted on exit |
| `--network=none` | no internet, no DNS, can't exfiltrate |
| `--memory=256m` | hard RAM cap |
| `--cpus=0.5` | half a CPU |
| `--pids-limit=50` | no fork bombs |
| `--read-only` | filesystem is read-only |
| `--tmpfs /tmp:size=64m` | small writable scratch for libraries that need /tmp |

Plus our own 30-second wall-clock timeout, in case the container
somehow ignores SIGTERM.

### How we get the docker CLI without a 177MB apt install

```dockerfile
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
```

Multi-stage `COPY --from` grabs just the CLI binary from the official
Docker image — same trick we use for `uv`. ~20MB instead of ~177MB.
No daemon installed in our image; the CLI talks to the **host's**
Docker daemon via the `/var/run/docker.sock` we mount in
`docker-compose.yml`.

This means our service containers can spawn sibling containers but
cannot run a Docker daemon themselves. Clean separation.

### Safety reasoning

Mounting `/var/run/docker.sock` is famously "root-equivalent" on the
host. Two reasons that's acceptable here:

1. **Only our Python code touches the socket** — never the LLM. The
   LLM only sees `tool_result` strings; we pre-construct the `docker
   run` invocation with hardcoded safe flags.
2. **The sandbox container itself has none of this.** Code running
   inside the sandbox doesn't get the socket, has no network, has
   read-only filesystem. So even if LLM-generated code is malicious,
   its blast radius is the throw-away container.

### Initial scoping choices

- **stdin-only delivery, stdout/stderr return.** Code goes in as
  stdin, output comes back. No file mounting. Simpler, no host-path
  translation headaches.
- **No artifact return path yet.** The sandbox is read-only, so code
  can't produce files the agent later reads. This means no
  chart-as-image workflows in Phase 5.3. Adding a writable shared
  tmpfs is documented as a follow-up in `IDEAS.md`.

### Cost: one image pull on first run

First call pulls `python:3.12-slim` (~50MB, ~5 seconds). After that,
the image is cached on the host and subsequent calls start in <500ms.

---

## What's Next

Up next from `IDEAS.md`: 5.4 (self-improvement loop), 5.5 (calendar),
5.6 (email triage).

### Highlights of `IDEAS.md`

- **Conversation continuity across services** — currently each service
  has its own Agent with its own history. You could finish a thought on
  Telegram and start the REPL fresh; the REPL agent doesn't recall what
  you just said on the phone (only what got remembered).
- **Side-LLM memory retrieval** — when memory grows past ~50 entries,
  inject just the relevant ones rather than the whole index.
- **Telegram inline-button approval for `shell_exec`** — would let the
  bot run commands with explicit user approval, the same as REPL.
- **Mid-session context compaction** — for long sessions that push the
  token budget.

### See also: `IDEAS.md`
Each deferred improvement says what it is, why we deferred it, and
when revisiting would be worthwhile.

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


## Phase 5.4 — Self-improvement Loop

### Goal

Once per day, the agent reviews **its own behavior from yesterday** and
writes itself feedback/project memories. No human in the loop, no new
service, no new external dependency. It learns from its own logs.

This is the meta layer: the agent is already producing artifacts
(daily logs, tool calls, replies) and already has a way to persist
learning (`remember()`). All that's missing is a recurring trigger that
says "look back, decide what's worth keeping, save it."

### The mental model

A normal heartbeat tick is forward-looking — "what should I do *now*?"
A reflection tick is backward-looking — "what did I do *yesterday*, and
what should I learn from it?" Same Agent class, same tools, same memory
instance — different prompt, different scope.

### Why piggyback on the heartbeat instead of a separate service

We considered a dedicated `reflector` daemon in docker-compose. Tempting
because the *concept* is clean: "this service does daily learning." But:

- We'd duplicate ~30 lines of daemon scaffolding (env loading, error
  handling, sleep loop)
- We'd have a 4th service to start/stop/monitor
- The heartbeat already has Agent + tools + memory wired up

Reusing the heartbeat costs ~40 lines of code total. The trade-off is
that one tick per day will be a reflection instead of a normal tick —
acceptable, since reflection IS the useful work for that tick.

### The trigger logic: simpler than cron

Cron-style scheduling for "once per day at 3am" would need a cron
library, timezone reasoning, "did I miss yesterday's run because the
daemon was down?" recovery logic, etc.

Instead: at the start of each tick, read `memory/_last_reflection.txt`.
If the stored date is older than today (or missing entirely), this
tick reflects. Otherwise, normal tick.

Consequences:
- **At most one reflection per calendar day** — the marker is set on
  success.
- **Self-healing** — if the daemon was down for 3 days, the next tick
  catches up automatically (today's date > stored date, so reflect).
- **No timezone math** — we use the host's local date string.
- **No cron config** — the existing 10-min heartbeat schedule is the
  carrier wave; the first tick of each new calendar day rides on it.

### The marker file

`memory/_last_reflection.txt` is a single line: `YYYY-MM-DD`. That's it.

```python
def get_last_reflection_date(self) -> str | None: ...
def set_last_reflection_date(self, date_str: str) -> None: ...
```

We compare strings, not datetime objects. `"2026-05-16" < "2026-05-17"`
works because ISO date strings sort lexicographically. Worth knowing as
a small ergonomic win — you can keep "is yesterday before today" logic
to a one-line `str` comparison anywhere in the codebase.

### The reflection prompt

Different shape from a normal heartbeat. It tells the agent exactly
which file to read (yesterday's log), what to look for (corrections,
mistakes, confirmations, ongoing project state), and what to do with
findings (`remember()` at most 3 things). It forbids workspace writes
and `notify()` so the reflection is silent — pure memory work.

The four "patterns worth carrying forward" categories map directly to
our existing memory `type` values:
- corrections → `feedback`
- non-obvious confirmations → `feedback`
- ongoing project state → `project`
- past-mistake-don't-repeat → `feedback`

This makes the prompt write directly to the typed-memory schema the
agent already knows.

### The branch inside `tick()`

```python
today = _today_str()
last = memory.get_last_reflection_date()
do_reflection = last is None or last < today

agent = Agent(memory=memory, model=model)
if do_reflection:
    # build reflection prompt, run, set marker, return
else:
    # build normal heartbeat prompt, run
```

Marker is set **after** `agent.chat()` returns successfully. If the
chat raises mid-tick (rate limit, network blip), the marker stays
stale and the next tick retries the reflection. Self-healing.

### What we didn't add

- **No reading older logs.** The prompt explicitly says "don't chain
  into older logs." Reflection is meant to be cheap and bounded — one
  log file per pass. Recurring patterns will naturally accumulate as
  memories over multiple days, so we don't need to re-scan history.
- **No diff against existing memories.** The prompt says "skip anything
  trivial or already covered by an existing memory in your index."
  We trust the index (already in system prompt) to dedupe at the LLM
  level rather than building deterministic dedup.
- **No quality scoring.** No reranking, no "did this memory help"
  feedback. If memories pile up uselessly, we'll address it then.

### The autonomy story

Phase 1: it could think and call tools.
Phase 2: it could remember.
Phase 3: it could act unprompted.
Phase 4: it could be reached from anywhere.
Phase 5.1: it could schedule itself.
Phase 5.2: it could research.
Phase 5.3: it could compute.
**Phase 5.4: it learns from itself.**

The loop is now genuinely closed — the agent's behavior today is shaped
by what it observed about yesterday's behavior. Not training in the ML
sense (no weight updates), but learning in the practical sense
(persistent rules saved to typed memory that influence future prompts).

### One subtle thing

The reflection prompt's instruction "save at most 3 memories" is a
**deliberate cap**. Without it, models will tend to over-summarize —
"the user prefers terse replies, the user likes Python, the user is
building Homunculus, the user…" — flooding the index with low-signal
entries that drown the high-signal ones. Bounding the output count
forces the model to triage. A small prompt-engineering choice with a
large quality effect.


## Phase 6.1 — Obsidian-compatible Memory Vault

### Goal

Make `memory/` openable as an Obsidian vault — without changing how the
agent reads or writes memory. Obsidian gives us graph view, backlinks,
and (with Syncthing/iCloud) mobile read access to every memory and log,
all for ~50 lines of code.

### What Obsidian actually buys us

Obsidian is a free local-first markdown editor. Open any folder of `.md`
files as a "vault" and Obsidian renders frontmatter, resolves
`[[wikilinks]]`, builds backlinks, and draws a graph view of how notes
connect. Their mobile app reads the same folder if it's synced (iCloud,
Dropbox, Syncthing).

This is **not** a behavioral change for the agent. The agent still
reads `MEMORY.md`, calls `read_file`, calls `remember()` exactly as
before. Obsidian is purely a viewing/exploration layer on top of the
same files. Nothing in `core.py`, `heartbeat.py`, or `telegram_bot.py`
needed to change.

### The three concrete changes

**1. Dual-link MEMORY.md entries.** Each index line now carries both a
standard markdown link AND an Obsidian `[[wikilink]]`:

```
- [User Role](./user_user_role.md) — Senior PM, ... [[user_user_role]]
```

The markdown link is for our regex parser and plain-text viewers. The
wikilink is what Obsidian's backlink resolver picks up. Two link styles,
one line — neither breaks the other. Critically, the existing
`_annotate_entry` regex still matches because it parses the markdown
link form and treats the rest as description.

**2. Optional `related` arg on `remember()`.** The tool now accepts an
optional `related: list[str]` of memory slugs. We:

- Store them in YAML frontmatter (`related: [user_role, project_homunculus]`)
- Append a `## Related` section at the bottom of the body with
  `[[wikilinks]]` so the graph view shows the edges

We deliberately keep `related` **optional** so the agent can save
quick memories without thinking about graph topology. When it
naturally connects two things, it links them; otherwise no friction.

**3. Auto-generated `memory/README.md`.** First time `Memory.__init__`
runs, it drops a schema doc into `memory/README.md` explaining the
four memory types, the layout, and how to use the folder as a vault.

Obsidian sorts files alphabetically, so `README.md` appears near the
top of the file list. Whoever opens the vault (you on your phone, a
future LLM session, a collaborator) sees the schema explained before
they see any individual memory. This is borrowed directly from
Karpathy's LLM-wiki pattern (`CLAUDE.md` at the root explains the vault
to any agent that visits).

The README is created only if it doesn't exist — your local edits stick.

### What we borrowed from Karpathy's wiki pattern

Looked at an existing personal wiki implementing Karpathy's "LLM Wiki"
idea: typed folders (`concepts/`, `entities/`, `analyses/`, `sources/`),
an `index.md`, append-only `log.md`, wikilink-first navigation, and a
`CLAUDE.md` schema doc at the root.

Borrowed:
- **Schema doc at the root** (our `README.md` plays this role)
- **`[[wikilink]]` style everywhere** for Obsidian graph
- **Explicit `Related:` cross-references** as a frontmatter field

Did NOT borrow:
- **Typed folders.** Our memories are already typed via filename prefix
  (`user_`, `feedback_`, `project_`, `reference_`) and frontmatter
  `type:`. Adding folder buckets would duplicate that classification.
- **`raw/` ingest queue.** That pattern is for compiling external docs
  into knowledge pages. Homunculus's memory comes from live
  conversation, not document ingestion — different shape.

### How to use the vault

1. Install [Obsidian](https://obsidian.md) (free).
2. "Open folder as vault" → point at `homunculus/workspace/memory/`.
3. The graph view shows memories connected by `[[wikilinks]]`.
4. For mobile: enable any folder-sync solution on `workspace/memory/`
   (iCloud Drive, Dropbox, Syncthing) and open the same folder in
   Obsidian Mobile.

`.obsidian/` is gitignored — your local Obsidian config doesn't
pollute the repo.

### The honest assessment

Obsidian doesn't make Homunculus smarter. It doesn't add new
capabilities. It's a viewing layer.

What it DOES give you:
- A nice mobile-read interface for memory (free, no servers, no auth)
- A graph view that occasionally surfaces non-obvious connections
- Better markdown rendering than VS Code's raw view
- A future-proofing benefit: the day you want to leave Homunculus,
  your memory is still just a folder of markdown files

What it does NOT give you:
- A way to write memory from the Obsidian app (one-way only — agent
  writes, you read)
- Automatic sync (you provide the sync layer)
- Any change in agent behavior

For day-to-day Telegram use, you'll rarely open Obsidian. For
"what does my agent know about me?" introspection, the graph view is
genuinely useful.


## Phase 6.2 — Live Thinking Feed

### Goal

A browser page that shows what the agent is doing in real time across
all three services (REPL / heartbeat / Telegram). Every user message,
every tool call, every tool result, every reply — streamed live.

Open `http://localhost:8765` and watch your agent think. This is the
single best demo of the whole system.

### The architecture (deliberately boring)

```
   services emit  →  workspace/_events.jsonl  ←  feed.py tails
                                                       ↓
                                                  SSE stream
                                                       ↓
                                                browser EventSource
```

Three observations make this work:

1. **All services already share `workspace/` as a volume.** We don't
   need a new transport — just a file at a known path inside it.
2. **JSONL appends are atomic at the kernel level for small writes** —
   multiple services can append concurrently without corruption.
3. **SSE = long-lived HTTP response with `text/event-stream`.** No
   protocol, no upgrade dance — just lines of `data: {...}\n\n` over
   HTTP.

No Redis, no message broker, no websockets. The shared volume IS the
message bus.

### Why SSE over WebSockets

| | SSE | WebSockets |
|---|---|---|
| Direction | server → client only | bidirectional |
| Transport | plain HTTP | upgrade handshake |
| Auto-reconnect | built into browser | hand-rolled |
| Proxy compatibility | universal | sometimes blocked |
| Lines of code | ~20 | ~80 |

The feed is one-way (server pushes events to viewer; viewer never sends
anything). SSE is the right cut — anything else is over-engineering.

### Three small modules

**`events.py`** — single function `emit(event, **fields)` that appends
one JSON record per call to `workspace/_events.jsonl`. Writes are
best-effort: any IOError is swallowed so logging can never break the
agent's actual work. Each service identifies itself via the
`HOMUNCULUS_SERVICE` env var set in docker-compose.yml (`repl`,
`heartbeat`, `telegram`).

**`core.py` instrumentation** — four call sites in the agent loop:
`user_message` before entering the turn, `tool_call` + `tool_result`
around each tool dispatch, `assistant_reply` when the loop returns
the final answer. About 6 added lines.

**`feed.py`** — FastAPI app with two endpoints:
- `GET /` returns the single-page HTML/JS UI inline (no separate
  static-files setup)
- `GET /events` returns a `StreamingResponse` whose body is an async
  generator that polls the JSONL file every 250ms for new lines and
  yields them in SSE wire format

The HTML is dark-mode monospace, 100 lines including CSS. It uses the
browser's native `EventSource` API — five lines of JavaScript to
consume the stream, render colored rows, and auto-scroll.

### Why poll the file instead of inotify

`asyncio.sleep(0.25)` in a loop costs essentially nothing and works
identically on macOS and Linux. `inotify` is Linux-only; `watchdog`
is a whole library; SIGIO is obscure. Polling at 4Hz reads the file
position with one syscall when there's nothing to read. The simplicity
is worth more than the ~1 syscall/sec overhead.

### Initial tail replay

When a client first connects, the SSE generator replays the last
**50** events from the file before entering tail mode. So if you open
the page mid-conversation, you immediately see context — not a blank
screen waiting for the next event.

Implementation is one `readlines()[-50:]` slurp followed by `seek(0,
SEEK_END)`. The trick is that the file pointer is positioned at end
*after* the replay so live tail picks up exactly where the replay
left off — no gap, no duplicates.

### The port collision

Initially I bound the feed to `0.0.0.0:8000`. Docker reported "port
already allocated" — another uvicorn (from a different project) was
on 8000. Moved to **8765** because:

- It's not a default for anything common (8000 is Django/FastAPI
  default; 3000 is React/Next.js; 5173 is Vite; 8080 is many things).
- "8765" reads like decreasing digits — memorable.
- Above 1024 so no root needed.

### What this unlocks for portfolio / demo

You record a 30-second screen capture: open the feed page, send a
question via Telegram on your phone, and the page lights up:

```
14:32:01  telegram  user →     what's the weather in tokyo this weekend
14:32:02  telegram  ↳ web_search   {"query": "Tokyo weather May 16 17 2026"}
14:32:04  telegram  ↩ web_search   Found 3 results from weather.com...
14:32:05  telegram  ↳ web_fetch    {"url": "https://weather.com/..."}
14:32:08  telegram  ↩ web_fetch    Tokyo May 16-17: highs 22-24°C...
14:32:10  telegram  ← reply        Tokyo's weekend looks warm and dry...
```

Anyone who watches that immediately gets what an agent is — multi-step
reasoning made visible. Far stronger than "look, the bot replied
correctly" because it shows the *process*, not just the output.

### Limitations (deliberate)

- **No filtering / search UI.** It's a chronological feed; that's it.
- **No event-level replay** — the file is the truth, the UI is just a
  view. If you want to re-watch yesterday, `cat workspace/_events.jsonl`.
- **No rotation.** The JSONL grows forever. For now this is fine
  (each event is ~100 bytes; thousands per day = single-digit MB/yr).
  Logged in IDEAS.md if it ever matters.

### One subtle thing

The "user_message" event for heartbeat ticks shows the full proactive
prompt template ("It's a scheduled heartbeat tick…"). That's accurate
— it really IS the prompt the agent received — but it floods the feed
on each tick. If this becomes annoying, the fix is either to skip
emitting `user_message` for non-interactive services or to tag the
event differently. Left as-is for now; it's honest about what the
system is doing.


## Phase 6.3 — Full Web UI (Mini scope + status panel + streaming)

### Goal

A single browser tab that gives you:
- **Chat** with the agent, replies streaming token-by-token
- **Live thinking feed** (Phase 6.2, now under `/feed`)
- **Memory browser** — list of typed memories, click to read body
- **Log viewer** — daily log files, newest first, click to read
- **Status panel** — per-service liveness pill in the header

All on `http://localhost:8765`. No build step. FastAPI + inline HTML/JS,
same stack as the feed. ~600 lines total in `feed.py`.

### The honest hard part: streaming + tool use

Streaming a plain chat reply is trivial — Groq sends
`data: {"delta": {"content": "Hello"}}` chunks; you forward each one.

But an agent turn might be a **tool call**, not a text reply, and tool
calls *also* arrive as deltas:

```
data: {"delta":{"tool_calls":[{"index":0,"id":"call_abc","function":{"name":"web_search","arguments":""}}]}}
data: {"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"q"}}]}}
data: {"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\":\\"tokyo\\"}"}}]}}
```

You only know after the stream finishes whether this turn was a tool
call or a final reply.

**Our compromise** (`call_llm_stream` in `core.py`):

- Yield content deltas immediately as `("content", "<text>")` — these
  go straight to the browser.
- Accumulate tool_call deltas silently into a dict keyed by `index`.
- At `[DONE]`, yield `("done", <assembled_message>)` so the caller can
  inspect tool_calls and dispatch them.

If the turn was a tool call, the user sees nothing arrive (content_acc
stays empty), then briefly waits while the tool runs (visible in the
live feed), then the *next* turn's reply streams normally. If the turn
was a final reply, words appear as they're generated.

This is the cleanest cut I could find. The alternative — non-streaming
detection turn followed by a second streaming turn — doubles latency
and token cost on every final reply for no real win.

### Architecture

```
   browser ⇄ feed:8765
              │
              ├── GET /          → chat page (sends POST /chat/send)
              ├── POST /chat/send → SSE stream of reply tokens
              ├── GET /feed       → live thinking feed page
              ├── GET /events     → SSE stream from _events.jsonl
              ├── GET /memory     → list memory entries
              ├── GET /memory/<f> → render one memory file
              ├── GET /logs       → list daily logs
              ├── GET /logs/<p>   → render one log file
              └── GET /status     → JSON per-service liveness
```

The feed service now reads the workspace volume (existing) **plus** the
Docker socket (new — chat may invoke `python_exec`) **plus** the .env
file (new — needs the LLM API key for chat). Three services were
already running with these mounts; the feed joins them.

### Why a shared session with REPL/Telegram

The chat agent calls `agent.restore_session()` at startup and
`memory.save_session()` after each turn. This is the same
`_session.json` the REPL and Telegram bot use. Result: you can start a
thought on the web, continue on your phone, finish in the terminal.

Concurrency risk: if you message Telegram while typing on the web at
literally the same second, one save wins and the other gets clobbered.
Realistically you won't be on two surfaces simultaneously. Logged as a
non-issue.

### Status panel without infrastructure

Service health detection without adding cron pings or container probes:
**read the freshness of each service's most recent event in
`_events.jsonl`**.

- Last event < 12 min ago → **live**
- < 60 min ago → **idle**
- ≥ 60 min ago → **stale**
- Never seen → **unknown**

The feed page polls `/status` every 10s; the four pills in the header
update colors based on the response. Zero new infrastructure — just
recency-of-existing-events. Heartbeat naturally emits at least every
10 min (its tick interval), so it shows live during normal operation;
Telegram emits only when messaged, so it sits at "idle" most of the
time (correct semantically — the bot is healthy but doing nothing).

The `_safe_subpath` helper rejects path traversal attempts
(`../../../etc/passwd` style) on the memory and log routes — table
stakes for any HTTP server that opens user-supplied paths.

### No build step is a feature

Total client code: ~150 lines of vanilla JS across two pages, no
imports, no transpiler, no `node_modules`. The chat client is one
`fetch()` to `/chat/send` plus a `ReadableStream` reader that splits
on `\\n\\n` boundaries and appends `data:` payloads to the reply
element. That's the entire streaming UX.

If we ever need richer interactions (memory edit, multi-session, file
upload), then it's time for a real framework. Today it would be
ceremony.

### What's deliberately NOT here

- **No auth.** Localhost only. If the user ever wants to expose this
  beyond localhost, the right move is an SSH tunnel or a reverse proxy
  with auth in front — not baking auth into the app.
- **No memory editing.** The browser shows memory; `remember()` writes
  it. Mixing the two creates conflict scenarios with the agent.
- **No multi-conversation.** Single shared session across all
  surfaces, like Telegram. If you need parallel conversations, that's
  a future iteration.
- **No rich markdown rendering in chat** — `white-space: pre-wrap` and
  done. If the agent writes a list, you see the dashes. Authentic to
  the underlying text and avoids markdown-rendering edge cases.

### Subjective: when to use which surface

- **Telegram** — when you're away from your laptop or want push
  notifications.
- **REPL** — when you're debugging the agent itself; raw print
  statements show in `docker compose logs`.
- **Web UI** — when you want chat + observability in the same view,
  on a laptop. Best for "agent does a multi-step research task and I
  want to watch the tool calls flow through the feed while the reply
  streams."

The three are intentionally redundant. Each surface has a moment
where it's the right one.


### Token-saving package (bundled in this PR)

The screenshot in dev — 8K TPM exhausted on the second "hi" turn —
forced the issue: our base payload (tool schemas + system prompt +
memory index + history) was ~3K tokens before the user typed
anything. We're on Groq's free tier (8K TPM for gpt-oss-120b), so two
turns and we're throttled.

Six concrete fixes shipped together so 6.3 is actually usable:

1. **Heartbeat default 10min → 60min.** The daemon was burning ~3K
   tokens every 10 minutes whether or not it did anything useful. 60
   minutes is enough cadence for a personal assistant. Override via
   `HEARTBEAT_INTERVAL_MINUTES`.

2. **`COMPACT_TRIGGER` 30 → 15, `COMPACT_KEEP_RECENT` 12 → 6.**
   History compacts much sooner. The conversational quality cost is
   minor (the compaction summary preserves the gist of older turns);
   the token saving is large because every turn includes the entire
   history.

3. **Memory index cap 30 → 15.** Smaller fixed cost per turn. Older
   memories still on disk, still discoverable via `read_file` when the
   agent specifically wants one — they just don't auto-appear in every
   prompt.

4. **Tool schema descriptions trimmed ~30%.** Removed redundant context
   that the LLM didn't need (e.g. the `remember()` description used to
   re-explain memory types that the system prompt already covers).
   Kept the constraints that actually shape behavior (python is
   sandboxed, notify interrupts, web_search must cite URLs).

5. **Provider fallback on 429.** New optional env vars
   (`HOMUNCULUS_API_KEY_FALLBACK`, `_URL_FALLBACK`, `_MODEL_FALLBACK`)
   that point at Gemini's OpenAI-compatible endpoint by default. When
   Groq returns 429, we transparently try Gemini instead. Gemini's
   free tier is **1M TPM** vs Groq's 8K — 125x headroom — so it
   absorbs bursts that would otherwise throttle us.

   Caveat: Gemini's OpenAI-compatibility layer is known to occasionally
   misformat tool calls on complex schemas. For final-text turns it's
   reliable; for tool-call turns it might emit a garbled call that we
   recover from. Acceptable trade for "user doesn't see 429".

6. **`.env.example` documents the fallback** with a link to
   https://aistudio.google.com/apikey (free key, no card).

### The honest takeaway on free-tier agentic systems

Even an "efficient" agent with tool use and memory eats ~3K tokens per
turn just for context. On a free tier with 8K TPM, that's about 2.5
turns/minute headroom — fine for one user typing, painful for
heartbeat + telegram + web chat all sharing the budget.

The fallback provider strategy buys real headroom without changing
quality on the happy path. Long-term, if this matters more, the next
move is either paying for higher Groq tier (~$10/mo) or running a
local model via Ollama (free, slower, no rate limits — but the user
explicitly ruled out local for this project).


## Phase 5.4.1 — Memory Hygiene

### Goal

A way for the agent to **delete its own stale memories**, modeled on
how Claude Code handles the same problem: write discipline + read-time
verification + sparse deletion, not time-based decay.

### What Claude Code actually does (researched, not guessed)

Looking at Claude Code's own system prompt and `/context` output:

1. **"Don't duplicate" rule at write time.** The agent is told: before
   calling `remember()`, scan the index. If an existing memory covers
   the same fact, update it (same name → overwrite) rather than create
   a new entry. This keeps memory growth slow.

2. **Hard line cap on `MEMORY.md` (~200 lines).** Anything past the cap
   is silently truncated when loaded into context. Working set bounded
   even if disk-bound entries pile up.

3. **"Update or remove" rule.** When the agent notices a memory is
   wrong or outdated, it's responsible for cleaning up.

4. **Verify-before-action.** A memory is a *claim* that needs
   re-checking, not ground truth. Stale entries get caught at use-time.

5. **No automatic deletion.** No background reaper, no time-based
   decay. Hygiene is a *judgment* the agent applies during its own
   work, not a cron job.

### What we already had

| Claude Code | Homunculus before this phase |
|---|---|
| "Don't duplicate" rule in prompt | Same-name upsert worked but no prompt rule |
| Index cap (~200 lines) | Index cap of 15 entries |
| `forget()` capability | **Missing — no way to delete from inside the agent** |
| Verify-before-action | Staleness markers (`⚠ may be stale`) but no explicit rule |

### Three small changes

**1. `Memory.forget(identifier)`** — accepts a name ("User Role") OR
a filename ("user_user_role" / "user_user_role.md"). Resolution tries
exact filename first, then name without `.md`, then slugifies the name
and probes each type prefix. Removes BOTH the body file and the index
line. **Idempotent** — already-gone memories return a benign status,
no exception. This matters: the agent shouldn't need try/except around
hygiene calls.

**2. `forget` tool** — schema with one string arg, registered in
`TOOLS`. The tool description deliberately includes "use sparingly —
when in doubt, leave it" so the LLM's behavior leans conservative.

**3. System prompt: "Memory hygiene" section** — four numbered rules:
   - Scan before write; reuse same name to overwrite, not duplicate
   - Forget contradicted/outdated memories when encountered
   - Verify "may be stale" entries against reality before acting
   - Be conservative — losing context is worse than carrying old facts

**4. Reflection prompt extension** — the Phase 5.4 daily reflection
tick now includes an explicit hygiene pass: scan for duplicates,
contradictions, irrelevant entries; `forget()` at most 2 per tick.
The "at most 2" bound prevents over-deletion sprees if the model has
a bad day.

### Why this preserves quality

- The agent only deletes things **it judges** obsolete. It has full
  conversation context for the judgment.
- The reflection tick is **bounded** (≤ 2 deletes/day) so accidental
  over-pruning is capped.
- Memory is **append-easy via remember()** — restoration after
  accidental deletion is one chat message away.
- The system prompt's "when in doubt, leave it" instruction biases
  toward keeping, not deleting.

### Why we deliberately did NOT add

- **Time-based automatic decay.** A 6-month-old `user_role` is still
  true. Age alone doesn't mean stale. Claude Code agrees — no decay
  in their design either.
- **LLM-as-judge background pruning daemon.** Already have the
  reflection tick; one daily pass is enough. A continuous reaper
  would be expensive and noisy.
- **A "deleted memories" trash folder.** Memories are markdown — easy
  to write back via `remember()` if a mistake happens. Trash adds
  complexity for a problem that doesn't materialize.

### The honest takeaway

**There's no magic auto-pruner.** The same is true at the Claude Code
scale. The strategy that works is: be careful when writing, verify
when reading, prune occasionally with judgment. We now have all three.

The "hard cap on index lines" is the safety net: even if hygiene
completely fails, only the 15 most-recently-touched memories make it
into the prompt. Older entries become silent and harmless.
