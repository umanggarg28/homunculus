# LEARN.md — Homunculus

> Covers: **Phase 1** (agent core), **Phase 2** (persistent memory),
> **Phase 2.5** (daily logs + age awareness), **Phase 3** (autonomous
> heartbeat), **Phase 4** (Telegram bridge), **Phase 5.0** (token
> efficiency: compaction, model override, index cap), **Phase 5.1**
> (self-scheduling heartbeat), **Phase 5.2** (web research).

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

## What's Next

Up next from `IDEAS.md`: 5.3 (code execution sandbox), 5.4 (self-
improvement loop), 5.5 (calendar), 5.6 (email triage).

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
