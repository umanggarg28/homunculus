# Production-Grade Quality Pass — Homunculus

## Context

Homunculus works (822 tests green, 6 services running) but its repo layout and
comment hygiene don't yet read as production-grade for a senior review. The
trigger: 21 domain modules sit loose at the repo root with no parent package,
sitting next to the already-packaged `tools/` and `transports/`. A field survey
of single-app Python agents — Letta (`letta/`), mem0 (`mem0/`), OpenHands
(`openhands/`), MetaGPT (`metagpt/`) — confirms the convention is **one
top-level package at repo root**, internals grouped by domain, never `src/`
for a non-distributed app. The monorepo agents (Hermes, Pi, OpenClaw, autogen)
split by *deployable app*, which doesn't fit a single app with multiple
transport entrypoints.

Alongside the structure, a machine-verified scan found: 5 dead-code items,
~8 files with dated/incident-narrative comments (against the standing
"timeless comments" rule), 67 OSS-pattern mentions to standardize (keep as
*idea-level* references — never code-copy claims), no linter config, and a
`print()` vs `logging` split between modules.

**Outcome:** a clean `homunculus/` package, consistent professional comments
that still credit pattern *ideas* for future reference, no dead code, a ruff
config, unified logging — and LEARN.md kept in lockstep as the build-from-
scratch guide. Behavior must not change; the 822-test suite is the proof.

## Locked decisions (from user)

- **Layout:** single flat `homunculus/` package at repo root.
- **Delivery:** phased PRs, each independently green.
- **Logging:** include the print()→logging migration (its own PR3).
- **OSS attribution:** keep idea-level references in a consistent form; reword
  anything implying code-copy. Taking ideas is fine; claiming copy is not.
- **LEARN.md:** first-class deliverable, updated in every PR.

---

## PR1 — Repackage into `homunculus/` (mechanical; no logic change)

Branch: `refactor/package-layout`

**Moves (via `git mv` to preserve history):**
- 21 root modules → `homunculus/` (core, heartbeat, memory, tasks, skills,
  quiz, config, events, messages, news_feeds, notifications, proposals,
  archival, skill_refiner, skill_validation, stats, stores, transcript,
  agent_controls, user_location, user_tz).
- `tools/` → `homunculus/tools/`, `transports/` → `homunculus/transports/`.
- Add `homunculus/__init__.py` (short package docstring, `__version__`).
- **Stay at repo root:** `tests/`, `scripts/`, `workspace/`, `web/`,
  `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `homunculus.yaml`,
  `AGENTS.md`, all `*.md`. (`workspace/scripts/*.py` are the agent's sandbox
  files, NOT our package — untouched.)

**Import rewrite (absolute `homunculus.` prefix, matching Letta/mem0):**
- `import <mod>` → `from homunculus import <mod>`
- `from <mod> import X` → `from homunculus.<mod> import X`
- `import tools…`/`from transports…` → `homunculus.tools…`/`homunculus.transports…`
- Relative imports *inside* `homunculus/tools/` and `homunculus/transports/`
  (`from . import _helpers`) stay valid unchanged.
- ~128 prod sites + test sites. Apply mechanically, then verify by import, not
  by eye.

**Delicate spots (the "double-check dependencies" zone):**
- `tests/conftest.py`: stub keys `"tools"`, `"tools.notify"`, `"mcp*"` →
  `"homunculus.tools"`, `"homunculus.tools.notify"`. `load_real_tool_submodule`
  path `tools/<name>.py` → `homunculus/tools/<name>.py`; module name
  `tools.<name>` → `homunculus.tools.<name>`.
- Tests using `spec_from_file_location(... , ".../tasks.py")` etc. → repoint to
  `.../homunculus/<mod>.py` (test_delivery_ledger, test_due_at_user_tz,
  test_bootstrap_*, test_workspace_sandbox, test_task_intake_clarifier,
  test_skill_auto_refinement, …). Each loads by path, so only the path string
  changes.
- `scripts/*.py`: imports → `homunculus.*` (they run with PYTHONPATH=/app).

**Docker / compose:**
- `Dockerfile`: replace `COPY *.py … ./` + `COPY tools/`/`COPY transports/`
  with `COPY homunculus/ ./homunculus/`; keep `COPY scripts/`, `homunculus.yaml`,
  `AGENTS.md`. Default `CMD` → `python -m homunculus.transports.repl`.
- `docker-compose.yml`: `transports.repl` → `homunculus.transports.repl`;
  `transports.telegram`/`discord` likewise; `python /app/heartbeat.py` →
  `python -m homunculus.heartbeat`; `uvicorn transports.web_api:app` →
  `uvicorn homunculus.transports.web_api:app`. `PYTHONPATH=/app` and
  `working_dir=/app/workspace` unchanged (package lives at `/app/homunculus`).
- No project install needed — `uv sync --no-install-project` stays; PYTHONPATH
  exposes the package.

**pyproject.toml:** no packaging change required (never installed), but add a
short `[tool.setuptools] packages = ["homunculus"]`-equivalent only if `uv run`
needs it; otherwise leave deps as-is. Decide during execution based on a real
`uv run` check, not assumption.

**LEARN.md:** rewrite `## 3. Project Layout` (line 68) to show the
`homunculus/` tree and the `python -m homunculus.transports.*` run commands;
fix any `python -m transports.*` references elsewhere in the doc.

---

## PR2 — Comment & dead-code hygiene + ruff config

Branch: `chore/comment-and-deadcode-hygiene`

**Remove 5 dead-code items (each re-checked in context first):**
- `homunculus/memory.py` — unused local `text_by_name` (verified: never read).
- `homunculus/proposals.py` — unused `from datetime import datetime`.
- `homunculus/tools/mcp_server.py` — unused import `ZoneInfoNotFoundError`
  (keep `ZoneInfo`).
- `homunculus/transcript.py` — unused `import os`.
- `homunculus/transports/web_api.py` — unused local `user_ts` (verified).

**Dated/incident comments → timeless** (keep the *why*, drop the "observed live
2026-06-NN"/"REMOVED 2026-…"/"discovered live" narrative): quiz.py, tasks.py,
memory.py, core.py, heartbeat.py, tools/news.py, tools/mcp_server.py,
transports/web_api.py. Incident history belongs in commit/PR messages.

**OSS-pattern comments → consistent idea-level form** (KEEP attribution; this is
intentional reference value): standardize to e.g.
`# Approach: <idea>. Similar to <project>'s <name>.` Reword code-copy phrasing —
notably `core.py:1256`'s literal `see llm_api_tools.py:200 in letta-ai/letta`
→ an idea-level note. ~67 sites across ~18 prod files.

**Lint config (new):** add `[tool.ruff]` to pyproject (`line-length = 100`,
curated select: `F,E,W,B,SIM,UP,C4,RET,PIE`, per-file-ignores for tests/E402),
add `ruff` to dev deps. Apply only safe fixes: `ruff check --fix` autofixables
+ manual `B904` (raise … from) for the 14 sites. **Do not mass-reflow the ~348
long lines** — note them as a deliberate follow-up to avoid churn/risk.

**LEARN.md:** add/refresh a short note in the relevant sections on the
pattern-lineage convention (how comments credit ideas) and the lint setup.

---

## PR3 — Unify on the `logging` module

Branch: `refactor/unified-logging`

- Convert ~50 `print(..., flush=True)` sites in `core.py`, `heartbeat.py`,
  `memory.py` to a module-level `log = logging.getLogger(__name__)` with
  `log.info/warning/error`. The existing `[call_llm]`/`[heartbeat]`/`[memory]`
  prefixes become logger names. Matches `notify`/`transports` which already use
  `logging`.
- Configure logging→stdout once per entrypoint (transports + heartbeat `main`)
  with a timestamped format, so Docker still captures everything.
- Safe: no test captures stdout (verified — no `capsys`/`capfd`), so output
  channel changes don't break assertions.
- **LEARN.md:** document the logging convention.

---

## Verification (run after each PR)

1. `uv run python -m pytest -q` → expect **822 passed, 32 skipped** every time
   (parity is the contract for PR1).
2. `uvx ruff check homunculus` (PR2+) → clean on the selected rules.
3. Container smoke test (PR1 + PR3): `docker compose build` →
   `docker compose up -d` → `curl -s localhost:8765/health` returns OK →
   `docker compose logs heartbeat` shows a clean tick → `docker compose ps`
   shows all 6 services up. Tear down with `docker compose down`.
4. Git history check: `git log --follow homunculus/core.py` shows pre-move
   history preserved (confirms `git mv`).

## Safety / non-negotiables

- `git mv` (not delete+create) so blame/history survive.
- Every dead-code removal re-confirmed in context immediately before deletion.
- One PR per concern; each green before the next. Branch → PR → `gh pr merge
  --merge --delete-branch` per repo workflow.
- No behavior change in PR1/PR2; PR3 changes only the log *channel*, not logic.
- LEARN.md updated within each PR, not deferred.
