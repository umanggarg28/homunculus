# Code review — 2026-08-18

A full read-through of the package, done for two purposes: decide what to refactor, and
build a from-scratch teaching path over the same material. Findings are grouped by kind and
each carries a severity and a fix sketch. The last section maps every finding onto the
learning level where it becomes an exercise.

**Reading depth.** Read in full: `core.py`, `llm.py`, `output_guard.py`, `task_guard.py`,
`permissions.py`, `events.py`, `locking.py`, `config.py`, `security.py`,
`tools/{__init__,mcp_manager,filesystem,_helpers}.py`. Structural sweep (docstrings,
signatures, targeted reads): `heartbeat.py`, `memory.py`, `tasks.py`, `skills.py`,
`web_api.py` + routers, transports, tests, CI, `web/`.

Severity is about consequence, not effort: **HIGH** = wrong behaviour reaches the user or
money/data is at risk; **MED** = drift that will cause a HIGH later; **LOW** = tidiness.

---

## 1. Live defects

### 1.1 Semantic recall is silently dead in production — HIGH
`Memory._embed()` requires `GOOGLE_API_KEY` / `GEMINI_API_KEY`. Neither is present in the
running containers, so `_embed()` returns `None` and every `recall()` falls back to keyword
overlap. `memory.db` holds 18 vectors that no longer match the vault. Verified live against
the running web service.

The failure is invisible by construction: every DB and embedding path in `memory.py` swallows
its exception and returns `None`/`[]` with at most a `log.debug`. Compare `llm.py`, which
handles the identical situation correctly — `_warn_budget_degraded()` emits
`budget_accounting_degraded` once per process precisely because a silent fallback on the
budget ceiling is the failure worth shouting about.

*Fix:* emit a once-per-process `capability_degraded` event when the embedder is unavailable,
and add a `doctor.py` check. The missing key is an operations issue; the silence is the bug.

### 1.2 One failure vocabulary, three recognizers that disagree — HIGH
Tools signal an unreachable data source with an uppercase sentinel. There are seven, in **two
shapes**: underscore (`GMAIL_UNAVAILABLE`, `CALENDAR_UNAVAILABLE`, `NEWS_UNAVAILABLE`,
`LEETCODE_NEXT_UNAVAILABLE`) and spaced (`WEATHER UNAVAILABLE`, `CAREER CONTEXT UNAVAILABLE`,
`POSTING UNAVAILABLE`).

- `output_guard._UNAVAILABLE_SENTINEL_RE` = `^[A-Z][A-Z_]{3,}_UNAVAILABLE` — matches only the
  underscore family. A failed weather/career/posting call is therefore scored a **success**,
  never enters `TaskGuard._failed_tools`, and cannot trigger `every_required_source_failed`.
- `task_guard._FAILURE_SENTINELS` = `("NEWS_UNAVAILABLE", "WEATHER UNAVAILABLE")` — a notify
  carrying `GMAIL_UNAVAILABLE` or `CALENDAR_UNAVAILABLE` is **delivered to the user**.

This is the same class of defect as the six-day mail outage that recorded six successful
deliveries, reintroduced elsewhere because the vocabulary has no single home.

*Fix:* one module owning the sentinel grammar (both shapes) plus the canonical regex;
`output_guard` and `task_guard` import it. Ideally normalise the tools onto one shape.

### 1.3 SYSTEM_PROMPT hardcodes a stale tool list — HIGH
The "Honest refusals" section of `core.SYSTEM_PROMPT` enumerates "your registered tools
include create_task, notify, recall, read_file, write_file, web_search, web_fetch, python,
list_tasks". Observed 2026-08-18: asked to call `record_commitment`, the model replied that it
"isn't a tool I have" and listed exactly that set. A prompt section written to *prevent* false
refusals is now causing one.

*Fix:* render the catalogue from the live registry, or delete the enumeration and keep only
the rule ("check the catalogue rather than guessing").

---

## 2. The structural theme — tool policy lives away from the tool

### 2.1 Ten hardcoded tool-name lists across five harness modules — HIGH
`core` (`READ_ONLY_CACHEABLE_TOOLS`, `_TAIL_PRESERVING_TOOLS`, `_TERMINAL_TASK_TOOLS`),
`output_guard` (`_WEB_GROUNDING_TOOLS`, `_SCHEDULING_TOOLS`, `_CLAIM_TARGET_TOOLS`),
`permissions` (`MUTATING_TOOLS`, `_TEMPLATE_EXEMPT_TOOLS`), `security`
(`_UNTRUSTED_CONTENT_TOOLS`), `doctor` (`_HARNESS_TOOLS`) — plus `tools.ALWAYS_LOADED`,
`core._PER_TOOL_RESULT_CAPS`, `task_guard._FAILURE_SENTINELS`,
`heartbeat._REFLECTION_FORBIDDEN` / `_REFLECTION_CALL_CAPS`,
`output_guard._GUARD_TOOL_SUCCESS_PHRASES`, `permissions._IDENTITY_ARGS`.

**No test asserts that any of those names still exists in the registry.** Rename or remove a
tool and a guard silently stops guarding, with a green suite.

`mcp_server.py` already declares `readOnlyHint` per tool, and `READ_ONLY_CACHEABLE_TOOLS` is a
hand-maintained subset of exactly that — the duplication is live today.

*Fix, cheapest first:* (a) one test pinning every list to `tools.tool_names()`; (b) derive
what MCP annotations already know; (c) longer term, let a tool declare its own harness policy
so the loop is tool-agnostic.

### 2.2 The agent loop knows ~30 specific tool names — MED
`_dispatch_tool_calls` special-cases `load_tool`, skips `get_world_state`/`update_world_state`
for world-state stamping, and consults four name sets. A loop that must be edited whenever a
tool is added is not a loop, it's a switchboard.

---

## 3. Duplication that has already drifted

### 3.1 `call_llm_stream` re-implements the provider chain — HIGH
`_attempt_chain()` exists for the blocking path. The streaming path does not use it: ~100
lines of its own walk with `response_ctx` juggling and three separate cool-and-continue
blocks. It has already diverged — no second-pass retry, no `allow_primary_retry` gate, its own
budget check. It is also the least-covered code in the repo (see 5.2).

*Fix:* extract the chain walk so both paths share provider selection, cooldown, and budget;
streaming supplies only the response handling.

### 3.2 No paths module — HIGH (cheap)
`HOMUNCULUS_TASKS_DIR` appears inline 12×, `HOMUNCULUS_MEMORY_DIR` 12×,
`HOMUNCULUS_EVENTS_PATH` 6× — every one an `os.environ.get(...)` with a **cwd-relative
default**. This exact bug class has already been paid for twice: the stray repo-root
`proposals.json` and the stray repo-root `_events.jsonl` (44,771 events). `proposals.py`
already shows the right shape with `proposals_path()`; nothing else copied it.

*Fix:* `paths.py` with `tasks_dir()`, `memory_dir()`, `events_path()`, `proposals_path()`,
`workspace_root()`. One module, one default per path.

### 3.3 `TaskStore._locked()` is the last hand-rolled flock — MED
`locking.py` was created to end exactly this copy-paste, and its docstring says every store
delegates to it. `tasks.py` — the most contended file in the system — still has its own
50×0.1s retry loop. **`LEARN.md` (~line 1815) states `locking.py` is "the only copy of this
logic in the codebase", which is false.**

### 3.4 Four frontmatter parsers — MED
`memory._strip_frontmatter` (regex), `skills._parse_skill_frontmatter` (yaml),
`skill_validation._split_frontmatter` (yaml + error reporting), `web_api._parse_frontmatter`.

### 3.5 Tool results are truncated twice — MED
`tools.execute()` caps at `TOOL_RESULT_MAX_CHARS` (8000) with one marker; then
`core._trim_tool_result_for_history` caps again at per-tool limits (default 6000) with a
different marker and a head/tail rule. Two mechanisms, one job; the second always wins.

### 3.6 Two parallel hook mechanisms — MED
Module-global `tools._pre_execute_hook`/`_post_execute_hook` run inside `tools.execute()`;
Agent-scoped hooks run in `_dispatch_tool_calls`. The comment in `tools/__init__.py` says
"keep only one installed" — a footgun rather than a design. The global path is test-only
legacy. Same shape as the notify-suppression ContextVar incident.

### 3.7 `_drain_notifications_into_history` copy-pasted telegram ↔ discord — MED
Near-identical; the discord docstring says "Mirrors the Telegram bot". Both append straight to
`agent.history`, bypassing `_journal_append`, so drained notifications never reach the
transcript that the web chat history reads from.

---

## 4. Dead code that passes tests

### 4.1 `messages.py` — MED
279 lines of typed Pydantic message unions (`UserMessage`, `AssistantMessage`, …), imported by
**nothing but `tests/test_messages.py`**. `core.py` still passes raw `dict[str, Any]`
everywhere. The module reads as live design and is not.

### 4.2 `skill_contracts.py` — MED
`assert_registry_contracts()` exists to validate the skill registry at startup. It is called
only from `tests/test_skill_contracts.py`. A validation module nobody runs is worse than none:
CI is green *because* of the test, and the check never protects production.

*Fix for both:* wire up or delete. If `messages.py` is the intended direction, migrate
`core.history`; otherwise remove it and the test.

---

## 5. Process and test gaps

### 5.1 CI never touches the frontend — HIGH (cheap)
`web/` is ~18k lines of TS/TSX with its own `package.json`. CI runs ruff, pyright, pytest —
and nothing else. No `tsc -b`, no lint, no tests, no build. (It builds clean today; that is
discipline, not a gate.)

### 5.2 Coverage is inverted — MED
Overall 70%, but `heartbeat.py` 57% and `llm.py` 63% — the two files that can drop deliveries
and spend money are the least covered. `call_llm_stream` (llm.py ~1042–1189) is effectively
untested, and it is the duplicated one from 3.1.

### 5.3 No frontend tests at all — MED
Zero test files under `web/src`.

---

## 6. Module-level notes

**core.py (2247)** — module docstring still claims "the entire agent concept lives in
`Agent.chat()`. About 120 lines." `_dispatch_tool_calls` is ~250 lines with a six-branch
if/elif ladder resolving `result` (denied → cache → stuck → invalid args → hook-blocked →
suppressed → execute); extract a `_resolve_result()` returning `(result, kind)`. The
`active_schemas` block is duplicated verbatim across two branches of `_call_model`. Several
dead comment blocks remain where constants moved to `config.py`. `_clarify_before_act` is a
regex weak-model workaround and a retirement candidate. Incident-narrative comments ("Was
wasting 5+ LLM calls", "baseline probe #2", "Hit rate measured at ~40% on Gemini") violate the
repo's own comment rule.

**llm.py (1240)** — comment drift is significant enough to mislead: "Defaults target Gemini
2.5 Flash" (the default is deepseek), fallback slots documented as Kimi/Qwen when the default
string is gpt-oss:free + llama, "the primary (paid gpt-oss-120b)".
`measure_llm_usage_since()` reads the entire events file into memory per call while
`_scan_spend_bytes` directly above it does incremental byte-offset scanning — two scanners,
one naive. `_apply_reasoning_effort` only fires for `gpt-oss` model ids and is dead under the
current primary. `_PROVIDER_COOLDOWN` is per-process, so a 429 learned by heartbeat does not
bench that provider for web (the budget, by contrast, *is* shared through the events file).
httpx timeouts (60s/120s) are hardcoded while everything comparable lives in `config.py`.

**output_guard.py (710)** — `run_output_guard` is a 200-line linear sequence of ~15 checks;
a registry of check callables would make each one testable and instrumentable (the same shape
as the validators now in `permissions.py`). Job-application logic is embedded in the generic
guard (`draft_answer`/`prepare_application` result-string matching on prose like "Still
needing answers"). `_CLAIMS_DRAFTING_DONE_RE` is *used* at ~line 446 but *defined* at ~line
665, via `__import__("re").compile(...)` despite `import re` at the top. There is no
per-violation telemetry, so there is no way to know which of the ~8 phrase lists still earn
their keep on a strong model — instrument first, then prune.

**task_guard.py (562)** — solid. `_check` is an if/elif ladder over 7 criterion types; a
`{type: fn}` registry would make criteria pluggable, and the vocabulary is currently restated
in the class docstring and in `skill_validation`.

**memory.py (1082)** — five vector-DB helpers each open a fresh `sqlite3` connection with a
local `import sqlite3`; a cold `recall()` costs N+2 connections plus N serial embedding HTTP
calls. Every one swallows all exceptions (see 1.1). Still a god object after the store
extractions: vault + index + session + logs + embeddings + dedup + search.

**config.py (333)** — genuinely good (frozen pydantic, `extra="forbid"`, env-driven,
range-validated). Gaps: no paths section (3.2), and knobs still outside it — tool timeout,
`TOOL_RESULT_MAX_CHARS`, httpx timeouts, embed model, `MODEL`/`API_URL`, `WORKSPACE_ROOT`.

**mcp_manager.py (398)** — clean. No liveness check or restart for a crashed server: if a
subprocess dies, `_servers` keeps the stale entry and every call fails until the YAML changes.
`call()`'s 180s future timeout is dead — `tools.execute` wraps it in a 60s executor timeout.

**events.py (229)** — `emit()` now takes a cross-process flock per event on the hot path;
under 5s contention it raises and the exception is swallowed, so events are dropped with no
counter.

**web_api.py (719)** — app wiring plus replay building, chat-history filtering, memory
listing, rate limiting, SSE formatting. All 12 routers `import web_api as wa` and reach into
its globals (`wa._task_store()`, `wa.MEMORY_DIR`, `wa._chat_memory`); they should depend on a
services/deps module rather than on the transport.

**filesystem.py / _helpers.py** — path sandboxing (resolve + `relative_to` + prefix
stripping) is correct and is the strongest small module in the package. The `_SKIP` directory
set is duplicated three times within `filesystem.py`.

**Frontend** — `ChatPage` bundles to 639 KB (192 KB gzipped) with katex and highlight.js
loaded eagerly; both are lazy-load candidates.

---

## 7. Findings → learning levels

Each level: read the real file, rebuild the primitive from scratch, then fix the mapped
finding in the repo.

| Level | Subject | Exercise |
|---|---|---|
| 1 | The agent loop | retire `_clarify_before_act` (6.core); fix the module docstring |
| 2 | Tools & MCP | pin every tool-name list to the registry (2.1); collapse double truncation (3.5) |
| 3 | Persistence | `paths.py` (3.2); migrate `TaskStore._locked()` (3.3); one frontmatter parser (3.4) |
| 4 | Guards | unify the `*_UNAVAILABLE` vocabulary (1.2); check registry (6.output_guard) |
| 5 | Tasks & heartbeat | raise heartbeat coverage (5.2); settlement paths |
| 6 | Skills & self-improvement | wire up or delete `skill_contracts.py` (4.2) |
| 7 | Transports & web | de-duplicate the notification drain (3.7); routers → deps module (6.web_api) |
| 8 | Operations | frontend in CI (5.1); degradation events (1.1); share provider cooldown (6.llm) |

Unassigned but tracked: 1.3 (stale prompt tool list) and 3.1 (`call_llm_stream`) are worth
doing before their levels come up — the first because it actively misleads the model, the
second because it is untested duplication in the money path.
