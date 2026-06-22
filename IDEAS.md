# IDEAS.md — deferred work for Homunculus

Things we've considered and consciously deferred. Each entry: what, why
not now, and when it would become worthwhile.

---

## Memory improvements

### Side-LLM relevance retrieval
Instead of injecting the whole MEMORY.md index into every prompt, run a
separate cheap LLM call that takes the user's query + a manifest of all
memories and returns the 5 most relevant filenames. Only those get loaded.
A well-known pattern in production agent systems.

- **Why not now**: we have <20 memories. The whole index is ~1KB. Loading
  it costs nothing.
- **When to revisit**: memory exceeds ~50 entries, or the index alone is
  >5KB in a typical session.

### Memory file cap
Cap the number of memory files at 200, sorted newest-first by mtime. Older
entries either drop off or get archived.

- **Why not now**: see above. No volume.
- **When to revisit**: same threshold.

### Embedding-based semantic retrieval
Beyond LLM-as-retriever: embed each memory entry, embed the user query,
fetch top-K by cosine similarity. Faster than a side-LLM call, more
nuanced than keyword matching.

- **Why not now**: another moving part (embedding model, vector index).
  Not worth it at our scale.
- **When to revisit**: >500 memories, or retrieval latency matters.

### Memory pruning / decay
Delete or archive memories that haven't been read or updated in N months.
Keeps the working set small.

- **Why not now**: nothing to prune yet.
- **When to revisit**: ever first time the index feels noisy.

### SQLite as the memory store
Replace markdown files with a SQLite database. Allows fast queries by
type/date/keyword, vector search via sqlite-vec, etc.

- **Why not now**: markdown is human-readable, git-friendly, and the
  agent uses existing tools (read_file/write_file) to interact with it.
  SQLite would require a SQL tool and harder LLM mental model.
- **When to revisit**: >1000 memories, or we need analytical queries
  (e.g., "show me all feedback memories from the last 30 days").

---

## Conversation / context handling

### Mid-session compaction
When `self.history` grows past N tokens, summarize the oldest part into
a single message and replace the originals with the summary. Keeps the
context window bounded within a single session.

- **Why not now**: sessions are short for the moment.
- **When to revisit**: anyone hits MAX_TURNS or notices the API call
  getting slower due to history size.

### Resume previous session verbatim
Save `self.history` to JSON on exit, load on startup, prepend to the new
session. Gives perfect-fidelity "continue where we left off."

- **Why not now**: the KAIROS-style daily logs + reflection already give
  us continuity through distilled memory; raw replay is heavier and less
  scalable.
- **When to revisit**: if reflection turns out to miss too much detail
  and the agent feels amnesiac across sessions.

### Nightly distillation pass
A separate cron job (separate from the conversational reflection) that
reads the last 24h of logs and decides what to commit to typed memory.
Common pattern: distill raw logs into curated memory once per day.

- **Why not now**: our `reflect()` at session-exit covers the same
  ground for now.
- **When to revisit**: when Phase 3 (heartbeat daemon) lands — the
  heartbeat naturally provides the "nightly" hook.

---

## Tool / capability expansions

### `forget(name)` tool
Symmetric counterpart to `remember()`. Lets the agent (or user) remove
outdated memories.

- **Why not now**: memories are upserted by name, so updating happens
  naturally. True deletion is rare.
- **When to revisit**: when accumulated stale entries become an actual
  problem.

### `python_exec` writing files the agent can read
Right now `python_exec` runs in a read-only sandbox with no shared
volume — code can compute things but can't produce artifacts the
agent later reads. To enable chart-as-image workflows (the wow demo
from the original IDEAS list), mount a one-shot tmpfs into the
sandbox, then read any files that landed there after the run and
optionally surface them (e.g. send images via Telegram).

- **Why not now**: deliberate v1 scope — bidirectional file passing
  changes the safety model (sandbox can now write into our workspace).
- **When to revisit**: when the agent's first practical need is to
  produce an artifact (chart, parsed file, etc.) it can pass along.

### `http_get(url)` tool
Fetch web pages. Massive capability unlock — the agent could research
things, read docs, summarize articles.

- **Why not now**: scope creep for Phase 2. Phase 3+ territory.
- **When to revisit**: any time. Probably bundled with Phase 4 or earlier.

### Allowlist for `shell_exec` in autonomous mode
Phase 3 disables shell_exec entirely in autonomous (heartbeat) mode —
the LLM is told to leave a note via remember() instead. A more flexible
approach: an allowlist of pre-approved patterns (e.g., `ls *`, `cat *`,
`grep *`, `python *`) that the heartbeat could execute without
approval, while anything else still routes to remember().

- **Why not now**: blanket disable is the safer default; pattern
  matching for shell arguments is non-trivial to do safely (regex
  escapes, injection vectors).
- **When to revisit**: when the heartbeat genuinely needs to run code
  unattended (e.g., periodic test runs, log-tail scrapes). Build with
  a strict pattern language and a deny-by-default policy.

---

## Architecture / refactors

### Replace `tools.init(memory)` with proper dependency injection
Currently `tools/_state.py` holds a module-level Memory reference. Cleaner:
pass a context object to every tool function, or make tools instance methods
of an AgentContext class.

- **Why not now**: works fine with one piece of state. Refactor would
  be churn for no behavioral benefit.
- **When to revisit**: when we have a second piece of context (e.g.,
  Phase 4's Telegram client) that some tools need.

### Test suite
We have zero tests. The agent loop, memory operations, and tool
dispatch are all testable with mocked HTTP responses.

- **Why not now**: smoke-testing via the REPL is faster for a learning
  project at this stage.
- **When to revisit**: before merging any phase that adds significant
  complexity (Phase 3 heartbeat with timing, Phase 4 Telegram with state).

### Migrate from raw httpx to streaming responses
Currently we POST and wait for the full response. Streaming would let us
display the agent's reply token-by-token, like a real chat UI.

- **Why not now**: the API surface is simpler without streaming, and
  we're optimizing for readability.
- **When to revisit**: any Phase 4 UI work where latency feel matters.

---

## Telegram-related follow-ups

### Inline-button approval for `shell_exec` in the bot
Right now the Telegram bot disables `shell_exec` (no terminal for y/N).
A nicer design: when the agent calls `shell_exec`, the bot sends an
inline keyboard message with the command + "Approve" / "Deny" buttons.
User taps; the bot runs (or doesn't) and replies. Bridges the
interactivity gap properly.

- **Why not now**: scope creep on Phase 4. Want to ship & use it first.
- **When to revisit**: any time you find yourself wishing the bot
  could run shell commands too.


### Telegram message formatting via `parse_mode`
Currently we strip markdown artifacts on the way out so the message
goes as plain text. We *could* set `parse_mode="MarkdownV2"` instead
and have rich formatting — but MarkdownV2 requires escaping `.` `-`
`(` `!` and many other chars, and any miss crashes the send.

- **Why not now**: brittle. Plain text + cleanup is robust.
- **When to revisit**: only if rich formatting in the bot becomes
  genuinely valuable. Probably never.

---

## Phase 5 candidates ("wow" features, prioritized for build)

These are features we've decided are worth doing — listed in the order
we'd build them. Each one is its own feature branch + PR.

### 5.1 — Self-scheduling heartbeat
Drop the fixed `HEARTBEAT_INTERVAL_MINUTES`. Instead, the agent decides
when it should wake itself next: at the end of each tick it writes a
target wake time to memory (e.g., `next_wake_2026_05_18_0800.md`). The
heartbeat loop reads the soonest pending wake and sleeps until then.
Feels uncannily alive — the agent stops being a script and starts
having intentionality.

- **Effort**: ~half a day.
- **Why first**: foundational, modifies existing heartbeat, low risk,
  high "feels alive" payoff.
- **Gotchas**: need a fallback if the agent forgets to schedule
  itself (default to N minutes if no target found).

### 5.2 — Web research with citations
A `web_search(query)` tool. Use Tavily or Serper free tier (or DuckDuckGo
HTML scraping as last resort). Agent gets actual current info, returns
results with source links it cites in its replies.

- **Effort**: ~half to one day.
- **Why second**: biggest single capability unlock. Tokyo trip example
  works for real after this.
- **Pairs with**: Phase 5.3 if the agent needs to do anything with
  what it finds.

### 5.3 — Code execution sandbox
A `python_exec(code)` tool that runs Python in an ephemeral side
container (`docker run --rm --network=none python:slim`) and returns
stdout + any image written to a known output path. Lets the agent
make charts, compute things, debug its own scripts.

- **Effort**: ~1 day.
- **Why third**: very high visual impact (charts as images in
  Telegram), unlocks the agent to actually run the code it writes.
- **Gotchas**: container-in-container — needs the host's Docker socket
  mounted, OR a separate sandboxed runtime like microVMs.

### 5.4 — Self-improvement loop
A daily cron-style heartbeat tick that reads the last 24h of log files
and asks the agent: "review your behavior. What did you do well? What
did you miss? Write yourself a feedback memory." The agent learns from
its own mistakes. Meta loop.

- **Effort**: ~half a day, mostly prompt engineering.
- **Why fourth**: builds on existing log infrastructure, no new
  external dependencies, completes the autonomy story.

### 5.5 — Calendar awareness
Read Google Calendar events. New tool `upcoming_events(days=7)`. Agent
proactively notifies before events. "Tokyo flight in 3 days, 4 hours
until your client call."

- **Effort**: ~1 day. Google Calendar OAuth is the painful part.
- **Why fifth**: unlocks proactive scheduling but requires external
  API setup, depends on a working `notify()` (already done).

### 5.6 — Email triage
Connect to Gmail via IMAP or OAuth. Agent reads recent unread emails,
classifies, drafts replies you approve via Telegram (with inline
buttons — see "Inline-button approval" entry above).

- **Effort**: ~1-2 days. Gmail OAuth setup is the worst part.
- **Why sixth**: highest setup friction, but most "this actually saves
  me time" once working.
- **Depends on**: 5.7 (inline-button approval) for the approval flow
  to be clean.

---

## Phase 6 candidates (visual / portfolio polish)

### 6.1 — Obsidian-compatible memory vault
Point `HOMUNCULUS_MEMORY_DIR` at an Obsidian vault folder. Get all of
Obsidian's features for free: graph view, mobile viewer, backlinks,
markdown rendering, optional Syncthing sync to phone.

Tiny code changes:
- Add `.obsidian/` to gitignore
- Optionally emit both `[name](./file.md)` and `[[name]]` link styles
  so Obsidian's backlink resolver picks them up
- Document the setup (point the memory dir, install Obsidian)

- **Effort**: ~30 min.
- **Why**: free graph viz + mobile memory access. Replaces the
  "memory graph visualization" idea from the original list.

### 6.2 — Live "thinking" feed (web page)
A tiny FastAPI/Server-Sent-Events page that streams the agent's current
tool calls in real time. Open `http://localhost:8000` in a browser and
watch your AI think — `read_file(memory/...)` `remember(...)` etc., as
they happen across all three services.

- **Effort**: ~1 day. Need to expose Agent.chat to emit events to
  a queue that the FastAPI service drains over SSE.
- **Why**: pure demo wow. Show this in a Loom video and people get it
  immediately.

### 6.3 — Full UI (eventual)
A real web UI replacing/complementing the REPL. Chat interface,
memory browser, log viewer, agent status panel. Like a self-hosted
Claude Code.

- **Effort**: 1+ week.
- **Why later**: needs all the above functionality settled first.
  Building UI on shifting backend is wasted work. Once Phase 5 is done
  and the agent's behavior is what we want, then UI is the wrapper.
- **Stack candidates**: React + FastAPI, or HTMX + FastAPI for
  minimum complexity.

---

## When to use this file

When we discuss something that's interesting but out of scope, log it
here so we don't lose the idea. Each entry should answer: what is it,
why are we not doing it now, when would it become worthwhile?
