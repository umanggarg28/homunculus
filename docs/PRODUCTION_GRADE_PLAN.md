# Production-Grade Plan — from 7.5 to 9+

Source: principal-engineer review, 2026-07-02. This document is the working
reference for the hardening pass: every finding, its fix, and the PR that
lands it. Items are ordered by severity, grouped into PRs sized for review.

Grounding: where a fix has a canonical shape in the OSS agents this project
already studies (Pi, Letta, OpenClaw, Hermes), that shape is named on the
item. The rule from earlier passes stands: converge with what multiple real
projects do, don't invent.

---

## A. Bugs (correctness)

### A1. Prompt-leak canary is dead — HIGH
- **Where:** `core.py` — `_run_loop` calls `_prepare_turn` (builds the system
  prompt, embedding `self._turn_canary`) *before* it mints the fresh canary.
  The prompt therefore carries the previous turn's token (none on turn 1),
  while `_finalize_reply` checks the reply against the new token — one the
  model has never seen. The literal-canary leak check can never fire; only
  the fingerprint patterns work.
- **Why tests missed it:** `test_prompt_leak_canary.py` pins
  `agent._turn_canary` manually and tests the pure functions. The wiring —
  "the token in the prompt is the token we check" — was never asserted.
- **Fix:** mint the canary before `_prepare_turn`. Add a wiring regression:
  run a real `_run_loop` turn against a stubbed LLM and assert the canary in
  `history[0]` is the one the leak check uses (and that a reply echoing it is
  blocked).
- **PR:** `fix/canary-turn-ordering`

### A2. Run-now / chat hook cross-talk — HIGH
- **Where:** hooks (`set_pre_execute_hook` / `set_post_execute_hook` /
  `set_pre_turn_hook`) are module globals in `tools/__init__.py`. The web
  run-stream (`transports/web/tasks.py:159-161`) installs a TaskGuard as
  those globals **in the same uvicorn process that serves chat**.
  `_chat_agent_lock` serializes chat-vs-chat, not chat-vs-run-now. A chat
  turn during a manual task run gets the task's guard applied to its tool
  calls: its `notify()` can be blocked by the task's criteria, and its tool
  results pollute the guard's link-grounding blob.
- **Fix:** hooks become **run-scoped, owned by the Agent** — constructor
  parameters consulted by `_dispatch_tool_calls` and
  `_pre_iteration_injections`. The heartbeat, the reflection guard, and the
  web run-stream pass their guard to the Agent they create; the module-global
  setters remain as a back-compat fallback (tests use them) but no production
  path does.
- **OSS shape:** the run context owns its guard. Letta scopes handlers to a
  step; OpenClaw's cron guard lives inside the isolated agent it drives —
  neither installs process-global interceptors.
- **PR:** `refactor/agent-scoped-hooks`

### A3. Budget enforcement is off by default and leaks unknown paid models — HIGH
- **Where:** `llm.py`. `HOMUNCULUS_DAILY_BUDGET_USD` defaults to `0`, and
  `_budget_blocks_model` treats budget ≤ 0 as "no enforcement" — so out of
  the box there is no ceiling despite `enforce_daily_budget=True` and despite
  README/ARCHITECTURE claiming a "hard monthly cost ceiling" (it's daily).
  Separately, `_is_known_paid_model` returns False for any model missing from
  the hardcoded pricing table, so an unlisted paid model (`/use` in chat) is
  neither cost-counted nor budget-blocked — fail-open on exactly the
  expensive case.
- **Fix:**
  1. Unknown, non-`:free` models get a conservative default price — counted
     in spend and blockable. Fail closed on cost.
  2. One-time startup warning when enforcement is on but the budget is 0, so
     "off" is visible instead of silent.
  3. Docs say what the code does: **daily** budget, opt-in via
     `HOMUNCULUS_DAILY_BUDGET_USD`; `.env.example` documents it with a
     sensible value.
- **PR:** `refactor/llm-provider-chain`

### A4. `llm.py` retry pass duplicates the provider loop with weaker error handling — MEDIUM
- **Where:** `call_llm` lines ~703-757 are a near-verbatim copy of the main
  provider loop, minus the `try/except httpx.HTTPError` — a connection error
  during the retry pass escapes as a raw exception instead of the
  "All providers exhausted" failure shape.
- **Fix:** extract one `_attempt_chain()` (payload build + post + response
  classification) called by both passes. Introduce
  `ProviderExhaustedError(RuntimeError)` with the existing message so
  string-matching call sites keep working, and add the type name to
  `heartbeat._is_infra_error`'s markers so classification gets a typed anchor.
- **OSS shape:** one request path, typed failure classes (Temporal's
  retryable-vs-non-retryable split; LangGraph `RetryPolicy.retry_on`).
- **PR:** `refactor/llm-provider-chain`

### A5. Memory index/core block frozen at Agent construction — MEDIUM
- **Where:** `core.py` `__init__` bakes `load_core_block()` + `load_index()`
  into `_base_system_prompt`. The long-lived web chat agent never sees new
  memories in its index (or new pinned rules in its core block) until process
  restart. AGENTS.md got mtime-cached hot-reload; memory didn't.
- **Fix:** render the memory section inside `_current_system_prompt`,
  mtime-cached on `MEMORY.md` (every `remember`/`forget` touches the index,
  so its mtime is the change signal). Same pattern as the AGENTS.md cache;
  stays in the stable prefix for provider-cache friendliness.
- **PR:** `fix/memory-prompt-hot-refresh`

### A6. `chat.py` dead branch with operator-precedence confusion — LOW
- **Where:** `transports/web/chat.py:218` —
  `A in name or B in name and C in msg` groups as `A or (B and C)`, the first
  clause can never be true, and the branch is shadowed by the preceding `if`.
- **Fix:** collapse to one honest provider-outage mapping.
- **PR:** `refactor/extract-guards` (ride-along)

### A7. `MEMORY.md` upsert/remove asymmetry — LOW
- **Where:** `memory.py` — `_upsert_index_entry` rewrites the file as
  `_INDEX_HEADER + sorted entries`, discarding any hand-written non-entry
  lines; `_remove_index_entry` preserves them.
- **Fix:** upsert preserves existing non-entry lines (falling back to
  `_INDEX_HEADER` only when there are none).
- **PR:** `fix/memory-prompt-hot-refresh`

## B. Design / structure

### B1. Guard orchestration lives in the god-files — MEDIUM
- `Agent._output_guard` + eight correction-prompt constants belong in
  `output_guard.py` (the module exists; the orchestrator never moved).
  `TaskGuard` (~370 lines) belongs in its own `task_guard.py`.
- Thin delegating method / re-export stay so tests and imports keep working.
- Targets: `core.py` 2330 → ~2000 lines, `heartbeat.py` 1650 → ~1280.
- **PR:** `refactor/extract-guards`

### B2. String-matched failure classification — MEDIUM
- `heartbeat._is_infra_error` matches substrings ("API error") against a
  formatted exception string; a legitimate task failure mentioning an API
  error would be misclassified as infra and retried instead of feeding
  reflection. Typed `ProviderExhaustedError` (A4) gives it an exact anchor;
  the string markers stay as fallback for message-only paths.
- **PR:** `refactor/llm-provider-chain`

### B3. Spend accounting re-reads the whole events log per paid call — LOW
- `_today_spend_cents()` reads and parses all of `_events.jsonl` (2MB+) on
  every budget check. Fix: incremental cache — remember (offset, subtotal,
  window); on the next check read only appended bytes; reset when the file
  shrinks (rotation) or the local-midnight window rolls.
- **PR:** `refactor/llm-provider-chain`

### B4. Comment hygiene — LOW, continuous
- Stale forward references ("PR #112 will cut chat history reads over" —
  it landed) and dated incident narratives violate the project's own comment
  rule (timeless rationale in code; history in commits/PRs). Rewrite in every
  file a PR touches; no dedicated sweep PR.

## C. Documentation

### C1. LEARN.md — full rewrite, not a patch — HIGH
Constraints (owner's words): it is the **rebuild-from-scratch tutorial** — a
reader should be able to reimplement the entire app from it and understand
every bit of code, **without spending weeks reading**. Current state fails
both directions:
- **Self-contradiction:** §2 calls MCP a gap and §9 + the roadmap plan it as
  future work, while §6 (and the code — `tools/mcp_server.py`,
  `homunculus.yaml`) say it's done.
- **Structural rot:** two sections numbered §11; §2 points at "the roadmap in
  §10" which is the Test Harness; "Multi-provider fallback" appears twice in
  §4 with conflicting provider chains.
- **Stale facts:** `MAX_TURNS = 20` and "capped at 15 user turns" (both live
  in `config.py` now, with different defaults); "Groq — primary" (primary is
  paid `gpt-oss-120b` via OpenRouter); "3 test files" (there are ~106).

Rewrite shape: a **layered build order** — each layer is "what you build,
why, the minimal code shape, and the failure that motivates the next layer."
Loop → tools/MCP → memory → tasks+heartbeat → guards/settlement →
self-improvement → transports → hardening (locking, budget, security).
Keep the excellent existing deep-dives (locking, sources-are-data,
one-execution-core, guard tables) as the layer bodies; cut duplication;
every code claim re-verified against the file it cites.
- **PR:** `docs/learn-md-rewrite`

### C2. ARCHITECTURE.md §7 misstates config — LOW
Says tuning values "come from `homunculus.yaml` with env overrides";
`config.py` is env-only and `homunculus.yaml` is exclusively the MCP server
registry. Fix the paragraph.
- **PR:** `docs/learn-md-rewrite`

### C3. README/ARCHITECTURE budget wording — LOW
"Hard monthly cost ceiling" → daily, opt-in (matches A3).
- **PR:** `refactor/llm-provider-chain`

## D. Explicitly deferred (not this pass)

- Capability extension (real-world tool surface: Gmail/Calendar/GitHub MCP
  servers) — next phase, after the harness is at 9+.
- Per-`(user, channel)` sessions (Gateway phase) — unchanged from roadmap.
- Full comment-hygiene sweep of untouched files.

## PR sequence

| # | Branch | Items |
|---|--------|-------|
| 1 | `docs/production-grade-plan` | this document |
| 2 | `fix/canary-turn-ordering` | A1 |
| 3 | `refactor/llm-provider-chain` | A3, A4, B2, B3, C3 |
| 4 | `refactor/agent-scoped-hooks` | A2 |
| 5 | `fix/memory-prompt-hot-refresh` | A5, A7 |
| 6 | `refactor/extract-guards` | B1, A6, B4 |
| 7 | `docs/learn-md-rewrite` | C1, C2 |

Every code PR updates LEARN.md in the same PR where it changes something the
tutorial teaches. Exit criteria: three CI gates green, plus one live
regression turn (real model + MCP tools, temp workspace) covering tool calls,
memory write/recall, and a prompt-extraction probe proving the canary fires.
