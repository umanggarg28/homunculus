# Core Refactor Plan — Quality Pass (2026-06)

Reference plan for decomposing the project's three god-modules into focused,
single-responsibility units. Approved scope: **all 4 phases**. Each phase is its
own branch → PR → merge, with the full test suite green at every step.

## Hard constraints (non-negotiable)

- **Don't break anything.** Behavior is preserved exactly. The 896-test suite +
  `ruff` + `tsc` are the contract; they must pass before every commit and before
  every merge. CI (`lint-and-test`) must be green before merging any PR.
- **Branches, never direct to main.** One branch per phase, PR per phase.
- **Mechanical moves first.** Prefer pure relocation (cut/paste a function to a
  new module, re-import) over rewrites. No logic changes inside a move.
- **Incremental + reversible.** Migrate one domain/unit at a time; commit only
  when green. If a step can't be made green quickly, revert it, don't patch over.
- **LEARN.md stays in sync** within the phase that changes structure.
- **Redeploy** after each merge with `docker compose build homunculus && docker
  compose --profile discord up -d && docker builder prune -f` (keeps Discord
  current; reclaims build cache — see the disk-recovery runbook).

## Baseline (verified 2026-06-28)

- 896 passed, 32 skipped · ruff clean · tsc clean · CI green.
- 18,495 prod lines. Hot spots: `core.py` 3,509 · `web_api.py` 2,011 ·
  `heartbeat.py` 1,624.
- Good SRP examples to mirror: `approvals.py` (269), `failures.py` (50),
  `skill_contracts.py` (49), `memory_consolidation.py` (178).

## Findings (the "why")

1. **`core.py` god module (3,509 lines, 6+ responsibilities):** LLM HTTP client,
   provider fallback/budget/cooling, canary/prompt-leak security, output guard,
   tool-history handling, AND the `Agent` class.
2. **`Agent` god class (~1,600 lines, 21 methods); `_run_loop` is a single
   771-line method.**
3. **`web_api.py` flat (2,011 lines, 47 routes, zero `APIRouter`).**

OSS grounding: Letta separates `llm_api/` (client) from `agent.py`; Pi separates
its `ai` (provider abstraction) package from `agent` (loop); idiomatic FastAPI
uses one `APIRouter` per domain. Keep these as idea-level references.

---

## Phase 1 — `web_api.py` → domain `APIRouter`s  (LOW risk)

Branch: `refactor/phase-1-web-routers`

**Approach (break the import cycle with a shared context module):**
1. `transports/web_context.py` — move route-agnostic shared state + helpers:
   config constants (`MEMORY_DIR`, `TASKS_DIR`, `EVENTS_PATH`, `SPA_DIST_DIR`,
   `WEB_AUTH_TOKEN`, `QUICK_CAPTURE_TOKEN`, thresholds), `_chat_memory` (+ the
   one-time `tools.init`), `_chat_agent_lock`, `_get_chat_agent`,
   `require_web_auth`, `_task_store`, `_proposal_store`, `_known_tool_names`,
   `_safe_subpath`. Both the app and the routers import from here — one source of
   truth, no duplicate module-level state, no cycle.
2. `transports/web/` package with one router module per domain:
   `tasks.py` (9), `agent.py` (4), `proposals.py` (3), `chat.py` (3),
   `memory.py` (2), `chapters.py` (2), `mode.py` (2), `user_prefs.py`
   (user-tz/user-location), and a `misc.py` for singletons (status, stats,
   config, model, logs, context, containment, quick-capture, webhook,
   notifications, input-expected, events SSE). Each defines `router =
   APIRouter()`.
3. `web_api.py` becomes thin: build `app = FastAPI(...)`, `app.include_router(...)`
   for each, mount the SPA static handler **last** (catch-all `/{full_path}`
   must stay last so `/api/*` and `/events` win), keep `_lifespan`.

**Delicate spots:** route registration order (SPA catch-all last); the SSE
`/events` stream; the `_chat_agent` singleton + lock must live in ONE module;
domain helpers (`_build_agent_replay`, chat history/rate helpers, quick-capture
rate, webhook secret) move WITH their routers. Migrate one domain at a time;
`uv run pytest tests/test_web_api.py` after each.

**Done when:** all 47 routes serve identically, `test_web_api.py` green, app
imports clean, SPA still served at `/`.

---

## Phase 2 — extract `llm.py` from `core.py`  (MED risk)

Branch: `refactor/phase-2-llm-client`

Move the provider/HTTP/budget layer into `homunculus/llm.py`: `call_llm`,
`call_llm_stream`, `_providers`, `_expand_model_spec`, `_provider_key`,
`_budget_cents`, `_today_spend_cents`, `measure_llm_usage_since`,
`_budget_blocks_model`, `_cool_provider`, `_recent_provider_cool_seconds`,
`_is_transient_provider_error`, `_apply_*` payload helpers,
`_extract_assistant_message`, `_parse_retry_after`, `_emit_llm_call`,
`_serialize_messages`, `API_URL`/`MODEL` constants. `core.py` imports them.
Pure relocation; update imports across `web_api`, `heartbeat`, tests. Verify with
the full suite + a container smoke test (a real chat turn still completes).

---

## Phase 3 — extract `security.py` + `output_guard.py` from `core.py`  (MED risk)

Branch: `refactor/phase-3-guards`

- `homunculus/security.py`: `_make_canary`, `_canary_instructions`,
  `_detect_prompt_leak`, `_CANARY_RESPONSE`, `_wrap_untrusted_content`.
- `homunculus/output_guard.py`: `tool_result_indicates_failure`,
  `_claim_target_inconsistencies`, the output-guard phrase lists (moved to module
  constants — fixes the 247-line function smell), `_strip_citation_artifacts`.
Keep `Agent._output_guard` as a thin method delegating to the module. Re-point
`test_prompt_leak_canary.py` etc. (they import from `core`; keep shims or update
imports).

---

## Phase 4 — decompose the `Agent` class / `_run_loop`  (HIGH risk)

Branch: `refactor/phase-4-agent-loop`

The 771-line `_run_loop` becomes a small orchestrator over named, individually
testable phase helpers — modeled on `heartbeat.prepare_task_run /
build_task_guard / settle_task_outcome`:
- `plan` (build messages/system prompt/tool choice),
- `call` (one provider round-trip),
- `dispatch` (execute tool calls, evict/trim history),
- `guard` (output guard + self-correct + canary),
- `settle` (finalize reply / compaction).
Extract session/journal + compaction helpers (`_journal_*`,
`_rebuild_message_ids_after_compaction`, `_maybe_compact`, `_summarize_messages`)
into a `conversation.py` / `compaction.py` if they pull cleanly. Move the smallest
safe units first; keep `Agent`'s public surface (`chat`, `chat_stream`, `reflect`,
`reset`, `restore_session`) byte-identical. Most careful review; expect several
sub-commits.

---

## Verification protocol (run after EVERY step)

1. `uv run python -m pytest -q` → 896 passed, 32 skipped (parity is the contract).
2. `uv run ruff check homunculus tests` → clean.
3. `cd web && npx tsc --noEmit` → clean (Phase 1 touches no TS, but the web app
   must still build for deploy).
4. Per phase: container smoke test — `docker compose build homunculus`, bring up,
   `curl localhost:8765/health`/`/api/config` 200, a real chat turn completes,
   `docker compose logs heartbeat` shows a clean tick.
5. `git log --follow` on a moved file confirms history preserved (use `git mv`
   semantics where possible; for function moves, note the origin in the commit).

## Rollback

Each phase is one PR off main. If a regression slips through, revert the merge
commit; main returns to a known-green state. Never stack an unverified phase on
an unverified phase.
