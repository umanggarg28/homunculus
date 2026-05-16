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
Currently tools.py holds a module-level Memory reference. Cleaner: pass
a context object to every tool function, or make tools instance methods
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

## Phase 4 (still planned)

### Phase 4 — Messaging bridge (Telegram or Discord)
Bot that bridges your phone ↔ the agent. The heartbeat can push
proactive messages ("you asked me to check X — here's what I found").
Replaces the REPL as the primary way you interact when you're away
from your laptop.

---

## When to use this file

When we discuss something that's interesting but out of scope, log it
here so we don't lose the idea. Each entry should answer: what is it,
why are we not doing it now, when would it become worthwhile?
