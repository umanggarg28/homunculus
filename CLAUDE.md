# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Homunculus is a single Python package (`homunculus/`) wrapping a tool-calling LLM
(`deepseek/deepseek-v4-flash-0731` via OpenRouter, swappable via `HOMUNCULUS_MODEL`)
in the machinery to run it unattended: durable memory, scheduled tasks, a
background autonomy loop, self-authored skills, and chat over web/Telegram/Discord.
No agent framework — the loop is raw `httpx` + JSON. **Reliability is a property
of the harness, not the model**: a small open-weight model drifts and sometimes
fabricates, so the harness verifies, gates, and audits everything around it.

For full architecture detail, read `ARCHITECTURE.md` first — it is a reviewer-
oriented map of the package, the agent loop, and the reliability harness, kept
current. `AGENTS.md` is the agent's persona/identity layer (loaded into the
system prompt every turn) — edit it to change agent behavior/tone/rules, not
`core.py`. `LEARN.md` is a from-scratch tutorial narrative of the same system —
**update it in the same PR as any refactor** (the user studies it in parallel;
stale content is worse than none).

## Commands

Dependency management is `uv` (not pip/poetry/conda).

```bash
uv run ruff check homunculus tests scripts   # lint (matches CI)
uv run pyright homunculus                    # static type check, held at zero errors
uv run python -m pytest -q                   # full test suite (--cov-fail-under=60)
uv run python -m pytest tests/path/to/test_file.py::test_name   # single test
uv run python -m pytest tests/path/to/test_file.py -k "pattern"  # by name pattern
```

CI (`.github/workflows/ci.yml`) runs all three (ruff, pyright, pytest) on every
push to `main` and every PR. Tests run without Docker — `tests/conftest.py`
stubs the container-only deps (MCP).

Services run as modules, sharing the `workspace/` volume and `homunculus.yaml` +
`.env` config — no central orchestrator, coordination is through shared files:

```bash
docker compose up -d web              # REST + SSE + React console at :8765
docker compose up -d heartbeat        # autonomy daemon
docker compose run --rm homunculus    # interactive REPL
docker compose up -d telegram
docker compose --profile discord up -d discord   # profile-gated, off by default
```

Or directly: `python -m homunculus.transports.repl`, `python -m homunculus.heartbeat`,
`uvicorn homunculus.transports.web_api:app`.

Frontend (`web/`, React + Vite) has its own `package.json` — use `npm`/`vite`
inside that directory, not `uv`.

## Architecture essentials

- **The agent loop** (`homunculus/core.py`): `Agent.chat()` / `chat_stream()` →
  `_run_loop`, a thin orchestrator over named phases (`_prepare_turn`,
  `_loop_personality`, `_pre_iteration_injections`, `_call_model`,
  `_handle_tool_choice_violation`, `_dispatch_tool_calls`, `_finalize_reply`).
  Tool I/O is deterministic code in `tools/`; the model only supplies semantic
  parameters — never construct URLs/identifiers on the model's behalf, and
  never let the model guess an identifier (username, handle, feed URL) instead
  of reading it from config/memory or asking the user (see AGENTS.md's
  "Never fabricate identifiers").
- **Reliability harness** — the actual engineering thesis, weigh changes against it:
  - `permissions.py` — declarative gate consulted before every tool call: allow,
    deny (the reason becomes the tool result, so a refusal steers the model), or
    allow-on-corrected-arguments. Modes (`default`/`readonly`/`autonomous`/
    `bypass`) set a run's posture; rules are per-tool, first-match-wins.
    Normalizers run in every mode — repairing a malformed argument is
    correctness, not permission. Distinct from `Agent._pre_execute_hook`, which
    is run-scoped and dynamic (TaskGuard); the policy is static and runs first.
  - `output_guard.py` — cross-checks an assistant reply's action-claims against
    the turn's actual tool outcomes; refuses/self-corrects fabricated claims or links.
  - `proposals.py` / `approvals.py` / `skill_refiner.py` — self-improvement is
    human-gated: the agent proposes skill changes from its own execution traces,
    nothing takes effect until approved (web/Telegram/Discord). Never bypass this
    with a direct `write_file` to a skill.
  - `failures.py`, `heartbeat.py:_is_infra_error` — separates transient infra
    outages (retried/alerted, kept out of reflection) from genuine agent failures.
  - `security.py` — prompt-injection canaries; untrusted web/RSS content is
    wrapped before reaching the model.
  - `llm.py` — per-tick iteration budget, opt-in daily cost ceiling, multi-provider
    fallback chain.
- **Persistence** — everything durable is a plain file on the shared `workspace/`
  volume, no database: memory is a markdown vault (`MEMORY.md` index + typed
  entries), tasks are `tasks.json` under an `fcntl` flock (`locking.py:file_lock()`
  is the one cross-process primitive every store uses), events are append-only
  JSONL. Auditable with `tail`/`cat`, but single-host by design.
- **Config**: `config.py` is the single typed (pydantic) source of truth for
  tuning knobs, overridable by env vars. `homunculus.yaml` is exclusively the
  MCP server registry (hot-reloaded), not a tuning file. Secrets live in `.env`.
- **`homunculus/tools/`** — ~24 one-concern-each modules exposed to the model
  over MCP (`mcp_server.py`/`mcp_manager.py`), namespaced `{server}.{tool}` in traces.
- **`homunculus/transports/`** — thin channel adapters (repl, telegram, discord)
  that all delegate to `Agent`; `web_api.py` + `web/` (12 domain routers) backs
  the React console.

## Working conventions specific to this repo

- Real regressions for behavioral changes: a green pytest suite is not proof —
  drive an actual `Agent.chat()`/`chat_stream()` turn against a temp workspace
  with real model/tools (or a real multi-process run for concurrency claims).
- Skill changes must go through `propose_skill`, never a direct file write —
  that's the human-approval gate the whole self-improvement story depends on.
- No dated/incident-narrative code comments ("fixed when X broke on..."); explain
  code and rationale timelessly. Incident history belongs in the commit/PR, not the file.
- `ruff` ignores `E501` (line length) intentionally; don't reflow lines to fix it.
- Known large files being decomposed incrementally, not rewritten wholesale:
  `core.py` (~2.2k lines), `heartbeat.py` (~1.6k lines) — both have thin
  orchestrators over named phases already; further extraction should follow
  that same pattern rather than introducing a different structure.
