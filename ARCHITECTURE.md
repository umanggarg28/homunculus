# Architecture

A reviewer's orientation to Homunculus. The README explains *what it does*; this
document explains *how it is put together* and *why the structure is the way it
is*, so a new reader (or reviewer) can navigate the codebase in one sitting.

## 1. What this is, in one paragraph

Homunculus is a single Python package (`homunculus/`) that wraps a tool-calling
LLM (currently `deepseek/deepseek-v4-flash-0731` via OpenRouter — swappable via
`HOMUNCULUS_MODEL`, see `.env.example`) in the machinery that makes a small,
open-weight model useful unattended: durable memory, scheduled tasks, a
background autonomy loop, self-authored skills, and chat across web / Telegram /
Discord. There is **no agent framework** — the loop is raw `httpx` + JSON. The
central design bet is that **reliability is a property of the harness, not the
model**: nothing reads any individual unattended run, so an unverified claim
ships and a silently broken task is indistinguishable from a quiet one. The
harness therefore verifies, gates, and audits everything around the model. Note
that this bet is about the *operating mode*, not the model's strength — the
current primary is mid-pack against frontier models on public benchmarks and
well above the median for its size and price, and §5.1 reports which guards it
actually trips in production.
Quality-critical judgment calls (e.g. drafting job-application answers, see
§3 `tools/career.py`) can route to a stronger paid model per-call via
`HOMUNCULUS_DRAFTING_MODEL` while the routine loop stays on the cheap primary.

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
├── heartbeat.py         # autonomous loop: tick(), task firing, settlement
├── task_guard.py        # TaskGuard: delivery criteria enforced at notify/complete time
├── permissions.py       # declarative gate on tool execution: modes, rules, arg repair
├── doctor.py            # advisory startup audit of stored config (never blocks)
├── config.py            # single typed source of truth for all tuning knobs
│
│  ── persistence / state ──
├── memory.py            # markdown memory vault (MEMORY.md index + typed entry files)
├── stores.py            # small single-file stores (world state, next-tick, reflection, skill stats)
├── tasks.py             # structured task state (schedule, status, run history) — fcntl-locked
├── locking.py           # the one cross-process file_lock() primitive every store uses
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
├── security.py          # injection canaries, leak detection, untrusted-content wrap, secret redaction
├── output_guard.py      # action-claim verification, citation-artifact stripping, failure detection
├── failures.py          # infra-vs-genuine failure classification
│
│  ── support ──
├── events.py  notifications.py  messages.py  agent_controls.py
├── quiz.py    news_feeds.py     user_location.py  user_tz.py  logging_config.py
│
├── tools/               # ~24 tool modules (one concern each); _-prefixed = internal helpers
│   ├── notify.py  memory_tools.py  scheduling.py  authoring.py  report.py
│   ├── career.py  # career wiki lookup + job-posting parse + application
│   │               # drafting (prepare_application, draft_all_answers) —
│   │               # facts from files, judgment from the model, mechanism
│   │               # is scripts/apply_fill.py on the HOST (never submits)
│   ├── news.py  leetcode.py  weather.py  github.py …   # deterministic-fetch tools
│   └── mcp_server.py / mcp_manager.py / mcp_config.py   # MCP integration
│
└── transports/
    ├── repl.py  telegram.py  discord.py     # chat channels (thin; delegate to Agent)
    ├── web_api.py                            # FastAPI app + shared web helpers
    └── web/                                  # 12 domain routers (tasks, chat, feed, proposals, skills, …)
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
3. **`_pre_iteration_injections`** — per-iteration context maintenance (mid-loop eviction, goal re-injection, budget nudge) — pure side effects, no control flow.
4. **`_call_model`** — one LLM call via `llm.call_llm[_stream]`; returns the assistant message (or signals an empty stream).
5. **`_handle_tool_choice_violation`** — defense-in-depth when a provider ignored `tool_choice`; returns a retry signal so the `continue` stays visible in the loop.
6. **`_dispatch_tool_calls`** — executes each requested tool, caches results, records outcomes, counts terminal completions.
7. **`_finalize_reply`** — runs the output guard; either yields the verified reply or injects a self-correction and loops again.

The loop repeats call→dispatch until the model returns a plain text answer that
clears the guard. Tool *I/O* is deterministic code (`tools/`); the model only
supplies semantic parameters (the model never builds URLs/links itself). This
is the highest-leverage reliability pattern in the codebase and it does not
scale away with model strength: a stronger model composes a plausible URL more
convincingly, not less.

## 5. The reliability harness (the actual thesis)

This is what a reviewer should weigh most, because it is where the engineering
judgment lives:

- **Permission gate** (`permissions.py`): every tool call is checked before it
  runs. A policy can allow it, deny it (the reason becomes the tool result, so
  a refusal is a steering signal the model reads rather than a silent drop), or
  **allow it on corrected arguments**. That third outcome is the reason the
  module exists: elsewhere a malformed call costs a round trip — guard rejects,
  model reads, model retries — whereas a normalizer repairs a known-shape defect
  in place and the call proceeds. Modes (`default` / `readonly` / `autonomous` /
  `bypass`) set the posture for a whole run; rules are per-tool and first-match-
  wins. Distinct from `Agent._pre_execute_hook`, which is run-scoped and dynamic
  (the TaskGuard asking whether *this* run has met its criteria) — the policy is
  static and asks whether the call is permissible at all, so it runs first.
- **Output guard** (`output_guard.py`): an
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
- **Delivery-criteria floor** (`skill_validation.criteria_strength_errors`):
  success criteria are validated for *strength*, not just shape — a criterion
  set a failure notice could satisfy is rejected at proposal time. Paired with
  `TaskGuard.every_required_source_failed`, which blocks `complete_task` when
  every data source a task depends on failed: that is an outage, not a
  delivery, and it belongs in `record_failure`.
- **Startup posture audit** (`doctor.py`): write-time validation only protects
  what is written *after* a rule exists, so stored config drifts silently. The
  heartbeat re-audits it at boot and logs findings — advisory, never blocking
  (the shape Hermes and OpenClaw both converged on; an audit that can stop the
  agent booting is one an operator deletes).
- **Task settlement & silent-drop detection** (`heartbeat.py`): a fired task
  that produces no delivery is detected, settled, and (after repeated failure)
  auto-cancelled rather than silently looping.
- **Secret redaction** (`security.py:redact_secrets`, applied in `events.emit`):
  the event log is rendered in the web console and screenshots of that console
  are committed to a public repo, so a credential reaching the log has a path
  to the public internet. Provider error bodies and tool results are echoed
  verbatim, so the line is scrubbed on the way out — matched on each provider's
  own key prefix, and applied to the serialized record so a credential nested
  inside tool args cannot slip past. ~0.075 ms per event.
- **Prompt-injection defense** (`security.py`): canary instructions detect
  prompt-leak attempts; untrusted tool content (web/RSS) is wrapped before it
  reaches the model.
- **Budget gate** (`llm.py`): a per-tick iteration cap and an opt-in hard
  **daily** cost ceiling (`HOMUNCULUS_DAILY_BUDGET_USD`; unset = no ceiling,
  warned once per process). Paid models missing from the pricing table are
  costed at conservative default rates so the cap fails closed. A
  multi-provider fallback chain handles provider failures.

### 5.1 Which guards actually fire

The event log makes the harness auditable against itself, so the claim above is
checkable rather than asserted. Over 13.5 days of production (9,375 events,
2026-08-01 → 08-14, primary `deepseek-v4-flash-0731`):

| Guard | Fires | Reading |
|---|---:|---|
| Delivery verifier (`output_guard.py`) | 0 | across 59 assistant replies |
| Stuck-loop | 236 | same call repeated within a tick |
| Duplicate-call cache | 147 | result served without re-running |
| `tool_choice` violation | 1 | provider returned no call when one was required |

Two things follow. First, the model's observed failure mode is **repetition,
not invention** — 211 of the loop-guard fires are one skill re-proposing an
identical edit, which is what motivated the proposal dedupe and the criteria
floor. Second, the zero is weak evidence on its own: 59 replies is a small
sample, and a guard that never fires is indistinguishable from one whose
condition never arose. It is reported as *not yet load-bearing*, not as proof
of a model that never fabricates — the guard's own tests, not production
counts, are what establish it works.

Read the current numbers off the log rather than trusting this table:
`homunculus/evals.py` computes them. Note that `output_guard` is an
**overloaded event name** — `core.py` emits it for five tool-dispatch guards
and `output_guard.py` for a blocked reply — so every emitter tags itself with
`kind` (`cache_hit`, `stuck_loop`, `permission_denied`, `args_corrected`,
`name_syntax_leak`, `reply_blocked`). Filter on that tag, never on the
human-readable `text`: the scorecard's `guard_fires` excludes only `cache_hit`
(the harness saved a round trip; that is not a model defect), and deciding
that by substring meant rewording a log line silently moved a metric used to
compare models. Events written before the tag existed fall back to the old
text match so historical runs keep their original scores.

Refused replies (`reply_blocked`) are additionally counted on their own as
`reply_blocks` — a **total**, not an average. They are a subset of
`guard_fires`, split out on severity rather than category: a refused reply
means the model claimed work with no tool evidence behind it, which is both the
worst outcome here and the rarest. One occurrence among hundreds of loop fires
would not visibly move an average, so it is carried as a count and the console
renders it only when non-zero — a permanent `0` chip trains the eye to skip the
one number that must never be skipped.

### 5.2 Did the edit help? (measured skill evolution)

The agent proposes edits to its own skills from its own traces, and a human
approves them — but nothing ever checked whether an approved edit *helped*.
One skill reached version 12 that way. `evals.compare_versions` closes the
loop: runs are stamped with the skill version that produced them (the same
move as stamping `model`), the scorecard slices `by_version`, and the two most
recent versions are compared on contract compliance, guard fires, and cost.

The verdict (`improved` / `regressed` / `mixed` / `inconclusive`, plus a
-5..+5 score and a plain sentence) is **computed, never model-generated**. The
model wrote the edit; asking it to grade its own edit reintroduces exactly the
unverified self-report the rest of the harness exists to remove.

Most of the work here is refusing to answer when the data cannot support one:

* **Attribution.** Events carry a `task` stamp (`events.task_context`), because
  a time window is not attribution — the heartbeat interleaves tasks, and one
  reflection tick looping on a single skill put 214 guard fires inside every
  other task's window, which read as three unrelated skills regressing at once.
  Runs whose guard counts predate that stamp are reported but never scored.
* **Model held constant.** A version window that straddles a model swap
  measures the swap. Runs are filtered to the newer version's model; runs from
  before model tracking group under `""` and so compare against nothing.
* **Infrastructure excluded.** `partial` runs (transient provider/network
  failures) are dropped — a six-day mail outage is not an edit's fault.
* **A floor of 3 clean runs a side**, below which the verdict is
  `inconclusive`. Not significance — just the point under which one slow API
  dominates the result.

Backfill: version history records when each version went live, so runs
recorded before stamping are attributed by timestamp
(`infer_skill_version`). Runs older than the first archived version belong to
version 0 and are never credited to the first edit.

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
scattered as constants across eight modules). Values are typed pydantic
defaults overridable by environment variables; ranges are validated; tests
override the config object rather than monkeypatching modules. Secrets live
in `.env`. `homunculus.yaml` is exclusively the MCP server registry
(hot-reloaded), not a tuning-config file.

## 8. Testing & CI

- **~114 test files** under `tests/`, run with `pytest`. Container-only deps
  (MCP) are stubbed via `tests/conftest.py`, so the suite runs without Docker.
- **CI** (`.github/workflows/ci.yml`) runs three gates on every push to `main`
  and every PR (Python 3.12):
  1. **ruff** — lint + a curated correctness ruleset.
  2. **pyright** (basic mode) — static type checking on the package, held at
     **zero errors**.
  3. **pytest** — under `pytest-cov` with a `--cov-fail-under=60` floor (a
     ratchet against regression; current coverage ~63%).
- **Real regressions** beyond the unit suite are the standard for behavioral
  changes: drive a live `Agent.chat()` / `chat_stream()` turn (or a real
  multi-process run for concurrency) with the real model and tools against a
  temp workspace, asserting the actual behavior — not a mocked stand-in.

## 9. Known limitations (honest list for reviewers)

- `core.py` (~2.2k lines) and `heartbeat.py` (~1.6k lines) are still large.
  Their orchestrators are decomposed — `_run_loop` and `tick()` are thin over
  named phases — but `_dispatch_tool_calls` (~180 lines) is the next extraction
  candidate, and neither file has been split into multiple modules.
- ~110 broad `except Exception` sites. The audit so far added log breadcrumbs to
  the ones that silently degrade real behavior (e.g. the embedding-DB writes);
  the remainder are intentional (telemetry-emit guards, an autonomous loop that
  must survive a bad tick) but haven't each been individually annotated.
- Type hints are partial (~630 functions); pyright runs in *basic* mode, not
  strict — it catches None-access and wrong-argument bugs, not full coverage.
- Test coverage (~63%) is thin on the transports (the Discord channel in
  particular); the floor guards against regression, it isn't a quality target.
- `E501` (line length) is intentionally unenforced.
- Single-host by design: file-based state does not scale horizontally.

## 10. Where to start reading

1. `homunculus/core.py` — `Agent.chat` → `_run_loop` and the named phases it
   orchestrates (`_pre_iteration_injections`, `_call_model`,
   `_handle_tool_choice_violation`, `_dispatch_tool_calls`, `_finalize_reply`).
2. `homunculus/heartbeat.py` + `task_guard.py` — `tick()` and `TaskGuard` for the autonomy story.
3. `homunculus/output_guard.py` — the reliability thesis.
4. `homunculus/transports/web/` — how the routers map to the React console.
5. `LEARN.md` — a longer build-from-scratch narrative of the same system.
