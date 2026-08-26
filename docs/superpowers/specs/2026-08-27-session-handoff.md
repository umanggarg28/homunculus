# Session handoff — 2026-08-27

Working state for resuming the remediation programme. The design lives in
`2026-08-27-remediation-design.md`; this file records where we stopped and the
conventions in force.

## Required reading before resuming

The repo already contains the diagnosis and most of the backlog. Read these
before touching anything:

- `docs/CODE_REVIEW_2026_08_18.md` — severity-graded review of the package.
  Eight findings still open; three are HIGH and are folded into the phases.
- `docs/CORE_REFACTOR_PLAN.md` — the June decomposition, executed as PRs
  #237–#240. Its phase vocabulary and verification protocol are inherited by
  this programme rather than replaced.
- `docs/superpowers/specs/2026-08-27-remediation-design.md` — the design, with
  the triage mapping every inherited finding to a phase.

## Where we are

Brainstorming is complete and the outline is approved:

- Approach **B** (seams first, then unify) — chosen over unifying interception
  first, because refactoring the hot path needs a safety net that does not yet
  exist.
- Sequence approved: comprehension → correctness → capability.
- Pace approved: **small PRs, one concern each**, each reviewed.
- Design written to disk and self-reviewed.

**Next step:** owner reviews the design doc. On approval, invoke the
`superpowers:writing-plans` skill to produce the Phase 0 + Phase 1
implementation plan. Do not start coding before that plan is approved.

## Working conventions in force

- **No `Co-Authored-By` trailer on commits.** Reaffirmed 2026-08-26. The public
  history should read as the owner's own work.
- **Never push to `main`.** Feature branch → PR via `gh pr create` → merge with
  `gh pr merge --merge --delete-branch`.
- **Branch naming:** `feat/`, `fix/`, `docs/` + topic.
- **Verify before claiming.** `uv run python -m pytest -q`,
  `uv run ruff check homunculus tests scripts`, `uv run pyright homunculus`.
  Quote real output; never assert green without running it.
- **Use the codebase's audited functions**, not ad-hoc scripts, when measuring.
  Both wrong numbers this session came from hand-rolled one-liners.
- **Teach while building.** Explain architecture at the point of change.
- **`LEARN.md` updates in the same PR** as the code it describes.

## Deployment facts

- Services: `web`, `heartbeat`, `telegram`, `homunculus`, `discord`,
  `docker-proxy`. Image `homunculus:phase1`.
- Rebuild and redeploy: `docker compose build web heartbeat` then
  `docker compose up -d web heartbeat`.
- The container's app interpreter is `/app/.venv/bin/python` — the system
  `python` lacks dependencies. Task and eval inspection must use it.
- Workspace paths inside the container: `/app/workspace/tasks`,
  `/app/workspace/memory`, `/app/workspace/_events.jsonl`.
- Current image was built 2026-08-25 23:10 UTC and contains PRs #315–#320.

## Landed this session

| PR | Change |
|---|---|
| #315 | Google grant diagnosis |
| #316 | Three post-deploy trace failures, plus registering an audit that was defined but never called |
| #317 | Ground a committed event time in what the tools returned |
| #318 | Dedup a check-in on its derived time, not the model's wording |
| #319 | Remove a third party's name and address from a test fixture |
| #320 | Put a paid model in the fallback slot |

Also: cancelled three duplicate reminders through the app's own store; cleared
1.7 GB of Docker build cache; scrubbed identifying details from the #317 and
#318 descriptions.

## Open items

1. **`c459eea` commit message** still names a counterparty company. Removing it
   needs a history rewrite and force-push of a public `main` — owner's call.
2. **Six advisory `doctor` findings** at startup, all pre-existing: weak
   criteria on `email-event-watch`, 22 orphaned memories, three dangling memory
   links, one unsatisfiable criterion.
3. **`_MODEL_PRICING_CENTS` is stale** — deepseek listed at (14.0, 28.0) cents
   per 1M; OpenRouter now charges $0.06/$0.12. The table feeds the daily budget
   ceiling, so the ceiling is currently miscalibrated.
4. **`reasoning_effort` reaches no model in the chain.** `_loop_personality`
   computes it per turn; `_apply_reasoning_effort` discards it for anything
   without `gpt-oss` in its name. Stale since the primary changed on 2026-08-01.
   Slated for Phase 5, needs per-guard sign-off.
5. **Dead-code inventory** owed to the owner before any removal.

## Model and cost context

Primary `deepseek/deepseek-v4-flash-0731` scores 52 on the Artificial Analysis
Intelligence Index — equal to GPT-5.6 Luna at max reasoning effort, at roughly
an eighth of the blended price. Evaluated and rejected: Qwen3.8 27B (same 52,
8× the cost, half the speed) and Qwen3.8 Max (58, but 33× the cost, 20.9 tok/s,
and flagged by AA as very verbose). Budget ceiling agreed at $5/month; current
run rate is near $1. **The chain is settled — do not reopen it.**
