# Architecture

A reviewer's orientation to Homunculus. The README explains *what it does*; this
document explains *how it is put together* and *why the structure is the way it
is*, so a new reader (or reviewer) can navigate the codebase in one sitting.

## 1. What this is, in one paragraph

Homunculus is a single Python package (`homunculus/`) that wraps a tool-calling
LLM (`openai/gpt-oss-120b` via OpenRouter) in the machinery that makes a small,
open-weight model useful unattended: durable memory, scheduled tasks, a
background autonomy loop, self-authored skills, and chat across web / Telegram /
Discord. There is **no agent framework** — the loop is raw `httpx` + JSON. The
central design bet is that **reliability is a property of the harness, not the
model**: a 120B open model drifts, claims work it didn't do, and occasionally
invents data, so the harness verifies, gates, and audits everything around it.

## 2. Runtime topology

The app ships as **five long-lived processes**, all sharing one Docker volume
(`workspace/`) and one config (`homunculus.yaml` + `.env`). They are peers, not
a hierarchy — there is no central orchestrator process.

| Service (compose)   | Entrypoint                              | Role |
|---------------------|-----------------------------------------|------|
| `homunculus`        | `python -m homunculus.transports.repl`  | Interactive REPL (dev/debug) |
| `heartbeat`         | `python -m homunculus.heartbeat`        | Autonomous self-prompting loop; fires scheduled tasks |
| `telegram`          | `python -m homunculus.transports.telegram` | Telegram chat transport |
| `discord`           | `python -m homunculus.transports.discord`  | Discord chat transport (profile-gated) |
| `web`               | `uvicorn homunculus.transports.web_api:app` | FastAPI: REST + SSE feed + serves the React console |

**Coordination is through shared files, not a message bus.** Every process
appends JSONL to `workspace/_events.jsonl` (the live "thinking feed"); the web
service tails it and streams to the browser over SSE. Task state, memory, and
proposals are likewise plain files on the shared volume, guarded by `fcntl`
locks where concurrent writers exist (`tasks.py:_with_lock`). This is a
deliberate "no new infrastructure" choice for a single-host, tight-budget
deployment — see `events.py` and `tasks.py` docstrings.

## 3. Package map

```
homunculus/
├── core.py              # the Agent class + the tool-calling loop (the heart)
├── llm.py               # provider HTTP layer: call_llm / call_llm_stream, fallback chain, budget gate
├── heartbeat.py         # autonomous loop: tick(), task firing, TaskGuard, settlement
├── config.py            # single typed source of truth for all tuning knobs
│
│  ── persistence / state ──
├── memory.py            # markdown memory vault (MEMORY.md index + typed entry files)
├── stores.py            # small single-file stores (world state, next-tick, reflection, skill stats)
├── tasks.py             # structured task state (schedule, status, run history) — fcntl-locked
├── transcript.py        # conversation journal persistence
├── stats.py             # cost / usage accounting
│
│  ── self-improvement (all human-gated) ──
├── skills.py            # versioned procedural memory (skill_*.md + history/rollback)
├── skill_validation.py  # skill schema/contract checks
├── skill_contracts.py   # skill interface definitions
├── skill_refiner.py     # proposes skill repairs from execution traces
├── proposals.py         # proposal store (nothing applies without approval)
├── approvals.py         # resolve/apply approved proposals; emits proposal_resolved
├── memory_consolidation.py  # periodic memory dedupe/merge
├── archival.py          # cold-storage of stale state
│
│  ── safety harness ──
├── security.py          # prompt-injection canaries, leak detection, untrusted-content wrapping
├── output_guard.py      # action-claim verification, citation-artifact stripping, failure detection
├── failures.py          # infra-vs-genuine failure classification
│
│  ── support ──
├── events.py  notifications.py  messages.py  agent_controls.py
├── quiz.py    news_feeds.py     user_location.py  user_tz.py  logging_config.py
│
├── tools/               # ~22 tool modules (one concern each); _-prefixed = internal helpers
│   ├── notify.py  memory_tools.py  scheduling.py  authoring.py  report.py …
│   └── mcp_server.py / mcp_manager.py / mcp_config.py   # MCP integration
│
└── transports/
    ├── repl.py  telegram.py  discord.py     # chat channels (thin; delegate to Agent)
    ├── web_api.py                            # FastAPI app + shared web helpers
    └── web/                                  # 11 domain routers (tasks, chat, feed, proposals, skills, …)
```

The non-package surface stays at repo root: `tests/`, `scripts/`, `web/` (React
frontend), `workspace/` (gitignored runtime state), `docs/`, and deployment
files (`Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `homunculus.yaml`).

## 4. The agent loop (core.py)

`Agent.chat()` / `Agent.chat_stream()` are the only public entrypoints; both
funnel into `_run_loop`, which after the Phase-4 refactor is a thin orchestrator
over five named phases:

1. **`_prepare_turn`** — per-turn setup side effects (history, journaling, system prompt).
2. **`_loop_personality`** — picks `(tool_choice, reasoning_effort)` from the call source.
3. **`_call_model`** — one LLM call via `llm.call_llm[_stream]`; returns the assistant message (or signals an empty stream).
4. **`_dispatch_tool_calls`** — executes each requested tool, caches results, records outcomes, counts terminal completions.
5. **`_finalize_reply`** — runs the output guard; either yields the verified reply or injects a self-correction and loops again.

The loop repeats call→dispatch until the model returns a plain text answer that
clears the guard. Tool *I/O* is deterministic code (`tools/`); the model only
supplies semantic parameters — a key reliability pattern for weak models (the
model never builds URLs/links itself).

## 5. The reliability harness (the actual thesis)

This is what a reviewer should weigh most, because it is where the engineering
judgment lives:

- **Output guard** (`output_guard.py`, `core.py:_output_guard`): an
  action-claim is cross-checked against the turn's tool outcomes. A reply that
  claims work with no tool evidence behind it — or a fabricated link — is
  refused and self-corrected rather than sent.
- **Human-gated self-improvement** (`proposals.py` / `approvals.py` /
  `skill_refiner.py`): the agent proposes skill changes *from its own execution
  traces*; none take effect until the user approves (web panel, Telegram, or
  Discord). Skills are versioned with rollback (`skills.py`).
- **Failure classification** (`failures.py`, `heartbeat.py:_is_infra_error`):
  transient infra outages are retried/alerted and kept *out* of the reflection
  loop, so the agent doesn't try to "fix" problems that aren't its own.
- **Task settlement & silent-drop detection** (`heartbeat.py`): a fired task
  that produces no delivery is detected, settled, and (after repeated failure)
  auto-cancelled rather than silently looping.
- **Prompt-injection defense** (`security.py`): canary instructions detect
  prompt-leak attempts; untrusted tool content (web/RSS) is wrapped before it
  reaches the model.
- **Budget gate** (`llm.py`): a per-tick iteration cap and a hard monthly cost
  ceiling; a multi-provider fallback chain handles provider failures.

## 6. Persistence model

Everything durable is a **plain file on the shared volume** — no database.

- **Memory** → markdown vault (`workspace/memory/`): `MEMORY.md` index +
  typed `user_*` / `feedback_*` / `project_*` / `reference_*` / `skill_*`
  entries. Human-readable, Obsidian-compatible, git-diffable.
- **Tasks** → `tasks.json`, read-modify-write under an `fcntl` flock.
- **Small typed state** → `stores.py` (world state, next-tick wake, last
  reflection date, skill stats), each in its own file.
- **Events** → append-only `_events.jsonl`.
- **Proposals** → `proposals.json`.

The rationale (and trade-off) is explicit: files are auditable, debuggable with
`tail`/`cat`, survive process restarts, and need no extra service — at the cost
of being single-host and relying on file locks for concurrency.

## 7. Configuration

`config.py` is the single typed source of truth for tuning knobs (formerly
scattered as constants across eight modules). Values come from `homunculus.yaml`
with env overrides; ranges are validated; tests override the config object
rather than monkeypatching modules. Secrets live in `.env`.

## 8. Testing & CI

- **103 test files** under `tests/`, run with `pytest`. Container-only deps
  (MCP) are stubbed via `tests/conftest.py`, so the suite runs without Docker.
- **CI** (`.github/workflows/ci.yml`): `ruff check` + `pytest` on every push to
  `main` and every PR, on Python 3.12.
- **Real regressions** beyond the unit suite are the standard for behavioral
  changes: drive a live `Agent.chat()` / `chat_stream()` turn with the real
  model and tools against a temp workspace, asserting a real guarded reply.

## 9. Known limitations (honest list for reviewers)

- No static type checker in CI yet; type hints are partial (~630 functions).
- `core.py` (~2.2k lines) and `heartbeat.py` (~1.6k lines) remain large; the
  loop decomposition landed in `core.py`, `heartbeat.py` has not had the same pass.
- ~110 broad `except Exception` sites — partly intentional (an autonomous loop
  must survive a bad tick) but not yet individually audited/narrowed.
- No coverage measurement; `E501` (line length) is intentionally unenforced.
- Single-host by design: file-based state does not scale horizontally.

## 10. Where to start reading

1. `homunculus/core.py` — `Agent.chat` → `_run_loop` and its five phases.
2. `homunculus/heartbeat.py` — `tick()` and `TaskGuard` for the autonomy story.
3. `homunculus/output_guard.py` + `_output_guard` in core — the reliability thesis.
4. `homunculus/transports/web/` — how the routers map to the React console.
5. `LEARN.md` — a longer build-from-scratch narrative of the same system.
