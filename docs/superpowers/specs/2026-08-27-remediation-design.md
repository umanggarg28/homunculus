# Remediation design — quality, structure, and autonomy

Status: approved in outline, pending final spec review
Owner: Umang
Approach: **B — seams first, then unify**

## Why this exists

The package grew 27% in under two months and has never had a consolidation
pass. Of the last 60 commits touching `homunculus/`, **56 grew it and 4 shrank
it**; the largest reduction in its history is 27 lines, the largest addition
412.

The code is not careless — the system-prompt cache layering and the
per-iteration injections are both deliberate and well argued. The problem is
accretion: every trace-driven fix added a mechanism and none ever retired one.
That single fact accounts for all four symptoms:

- **Hard to follow** — 24,662 lines, 5,300 of them in three files; 22 functions
  over 100 lines, the largest being `_dispatch_tool_calls` at 315.
- **Each fix uncovers another bug** — new mechanisms interact with old ones, and
  old ones go stale silently.
- **Over-constrained model** — guards calibrated for a primary at Intelligence
  Index 24 still throttle one at 52.
- **Untrustworthy numbers** — the scorecard counts retry attempts, so it
  understates health by roughly 20 points.

Two bugs found before this work share one signature: **a mechanism that stopped
applying and said nothing**. An audit was defined but never registered; a
reasoning-effort guard kept matching on a model family that is no longer in the
chain. Neither raised, logged, or failed a test. Making that class loud is the
central robustness goal here.

## Constraints

1. **No capability is removed.** "Lean" means architecturally solid, not
   feature-stripped. Discord, every tool, and every skill stay.
2. **The live agent keeps working.** It holds real commitments; every step lands
   green and deployable. No long-lived broken branch.
3. **Small PRs, one concern each.** Reviewability is a requirement, not a
   preference — the user must be able to follow and veto each step.
4. **Behaviour-preserving unless stated.** A refactor PR changes structure or
   behaviour, never both.
5. **Comments carry no change history.** They explain what the code does and
   why. History belongs in commits. This is already in CLAUDE.md and is
   currently violated in roughly 15 places.
6. **Docs move with the code.** `LEARN.md`'s layer is updated in the same PR,
   per CLAUDE.md. `README.md`, `ARCHITECTURE.md`, `AGENTS.md` are checked for
   drift by any PR that invalidates them.
7. **Dead-code removal needs sign-off.** Inventory with usage evidence first;
   cuts ride inside the PR touching that file.

## How this is planned

This document specifies the whole programme; it is deliberately **not** one
implementation plan. Each phase gets its own plan when it starts, so no plan
outruns what the previous phase actually taught us. Phase 0 and Phase 1 are
planned together — the safety net exists to serve the first extraction.

## Phase 0 — the safety net

Characterisation tests for the turn lifecycle: given a scripted model response,
assert the exact sequence of phases, tool dispatches, guard fires, and history
mutations a turn produces **today** — including behaviour later judged wrong.
They pin current behaviour so a structural change that alters it fails loudly.

The existing 1,404 tests cover units well but almost nothing pins the loop end
to end, which is precisely the code Phase 1 moves. Nothing moves before this.

## Phase 1 — make the lifecycle readable

`core.py` already has the right shape: a thin orchestrator over named phases.
The fault is that they share one 2,375-line file and two exceed 250 lines.
CLAUDE.md directs that further extraction follow the existing pattern rather
than introduce a new one.

```
homunculus/agent/
    __init__.py      Agent — construction and public API only
    turn.py          TurnContext: the typed state a turn carries
    loop.py          the orchestrator, readable top to bottom
    prompt.py        system-prompt assembly and cache layering
    dispatch.py      tool dispatch
    injections.py    per-iteration context maintenance
    reply.py         finalisation
```

The load-bearing move is `TurnContext`. Phases currently communicate through
mutations on `self` plus passed-around locals. The state is smaller than it
looks: of 17 attributes assigned on `self`, most are construction-time config
written once. Genuine per-turn state is `history`, `_message_ids`,
`_turn_canary`, and `suppressed_calls` — a context of roughly six fields.

### Two coupling defects this phase must fix, not preserve

**Private symbols cross module boundaries.** `_evict_prior_tool_results`,
`_trim_tool_result_for_history`, `_validate_tool_args`, `_clarify_before_act`
and `_strip_citation_artifacts` are imported by other modules and by tests. A
leading underscore promises "internal" and these are not. They become public
functions in the module that owns them — history manipulation in
`agent/history.py`, argument checking in `agent/validation.py` — and importers
are updated in the same PR.

**`core.py` is an accidental re-export hub.** Other modules import `MODEL`,
`API_URL`, `get_config` and `tool_result_indicates_failure` *from core*, but
core imports them from `llm`, `config` and `output_guard`. Importers are
repointed at the real owners, so deleting a line from core can no longer break
an unrelated transport.

`homunculus/core.py` survives the split as a thin façade re-exporting `Agent`
and `SYSTEM_PROMPT`, because nine transports and several tests import from that
path. Repointing those is a separate, mechanical PR — keeping the façade means
the extraction PRs stay reviewable.

`heartbeat.py` (1,540 lines) and `llm.py` (1,399) receive the same treatment,
one PR each, after `core.py` lands. Order is forced: `heartbeat` imports
`core.Agent`, so core must be stable first. `llm.py` is the easiest — it imports
only `events`, `config` and `security`.

## Phase 2 — one interception contract

Four turn-scoped interception points exist today, and the defect is that they
return three incompatible shapes:

| Mechanism | Hook | Returns today |
|---|---|---|
| `PermissionPolicy.check(name, args)` | before_tool | `Decision` |
| `TaskGuard.on_tool_call(name, args)` | before_tool | `str \| None` |
| `TaskGuard.observe_tool_result(name, result)` | after_tool | `None` |
| `run_output_guard(reply, used, outcomes)` | after_reply | violations list |
| `_pre_turn_hook(idx, history)` | before_model | message dict |

`_pre_execute_hook` and `_pre_turn_hook` are not guards; they are the wiring by
which the above attach, and they disappear into the registry.

**Explicitly out of scope for this phase.** `security.py` is not interception —
`redact_secrets` and `loggable_tool_result` are logging hygiene invoked from
`events.emit`, and folding them into a turn middleware would be wrong.
`doctor.py` is startup-scoped, not per-turn. Both keep their current shape.

Mature frameworks converged on this contract: Google ADK's before/after
model-and-tool callbacks, LangChain's guardrails-as-code.

```python
class Middleware(Protocol):
    name: str
    def before_model(self, ctx: TurnContext) -> Intervention | None: ...
    def before_tool(self, ctx: TurnContext, call: ToolCall) -> Refusal | None: ...
    def after_tool(self, ctx: TurnContext, call: ToolCall, out: ToolResult) -> None: ...
    def after_reply(self, ctx: TurnContext, reply: str) -> Correction | None: ...
```

Guard *logic* does not change — only how it attaches. Ordering becomes one
explicit list instead of being implied by call-site placement.

## Phase 3 — liveness pinning

The robustness centrepiece, and the reason Phase 2 is worth its risk.

- **Static:** a test asserts every defined middleware appears in the registry —
  the `doctor` audit pin generalised to the whole guard surface.
- **Runtime:** each middleware records when it last fired. `doctor` reports any
  registered middleware that has not fired within the current `last_runs`
  window (20 runs), which is the same horizon every other health signal uses.

The runtime half would have caught the stale reasoning-effort guard on the day
the primary model changed.

## Phase 4 — trustworthy measurement

The scorecard reads `last_runs`, where a partial retries after 10 minutes up to
3 times (`partial_retry_minutes`, `max_consecutive_partials`). Each attempt is
recorded as a separate run, so one bad day counts 3–6 times and a good day
counts once.

Report per-occurrence rates alongside per-run. Also surface that `last_runs` is
a rolling window capped at 20, spanning ~10 days for daily skills and ~2 months
for weekly ones — `evals.py` already documents that aggregating across it blends
eras, and the dashboard aggregates anyway.

## Phase 5 — autonomy

Sweep for the stale-pin class. Make guards contingent on model capability rather
than model name, so a strong primary runs with looser reins while a weak
fallback still engages them. Unpin `reasoning_effort`, which `_loop_personality`
computes per turn and `_apply_reasoning_effort` then discards for every model in
the current chain.

**Each guard loosened needs explicit sign-off.** This trades safety margin on a
system holding real commitments, and that call is the owner's.

## Phase 6 — capability

Memory compounding: 29 entries hold 11 genuine cross-reference edges and 22
orphans, so recall cannot travel and the vault behaves as a flat list.

A noticing benchmark: seed a fixture mailbox with commitments — some plainly
stated, some oblique, some absent but tempting — and score recall and precision.
Precision is already measurable via the grounding guard. This measures the one
capability a chatbot structurally cannot have, and current evals measure
delivery rather than value.

## Exit criteria

A phase is done when these hold, not when the code looks finished.

| Phase | Done when |
|---|---|
| 0 | A characterisation suite drives a full turn through a scripted model and asserts phase order, dispatches, guard fires and history mutations. A deliberate reordering of the loop makes it fail. |
| 1 | No function in `homunculus/agent/**` exceeds 100 lines. `TurnContext` is typed and no phase reads turn state off `self`. No underscore-prefixed symbol is imported across a module boundary. |
| 2 | One registry lists every middleware in execution order. `permissions`, `TaskGuard` and `output_guard` attach through it and nowhere else. Guard behaviour is unchanged — the Phase 0 suite passes untouched. |
| 3 | A middleware defined but not registered fails CI. A registered middleware that has not fired within the `last_runs` window is reported by `doctor`. |
| 4 | The dashboard reports per-occurrence rates alongside per-run, and states the window it covers. |
| 5 | Every guard states which model capability it compensates for. `reasoning_effort` reaches the model. Each loosened guard has recorded sign-off. |
| 6 | Memory writes link to existing entries; orphan count trends down. The noticing benchmark reports recall and precision on a fixture mailbox. |

## Regression guards

Structural moves can silently degrade behaviour that no unit test covers. Each
is pinned before the move that threatens it.

- **Prompt cache ordering.** `_current_system_prompt` layers stable content
  before volatile content to keep the provider's prefix cache warm; measured
  hit rate is 51%. Moving it to `prompt.py` risks reordering. A test asserts the
  stable/volatile boundary, and cached-token ratio is checked after deploy.
- **Token usage.** Eviction timing determines per-call input size; average
  prompt is 14,691 tokens. Moving `_pre_iteration_injections` must not change
  it. Assert eviction fires at the same iteration for a fixed script.
- **Cross-process locking.** `locking.file_lock` is the single primitive behind
  `transcript`, `memory`, `agent_controls` and the task store. Nothing in this
  programme changes it.
- **Coverage of moved code.** Package coverage is 70.8%. Any PR moving code must
  not lower coverage of the files it touches.

## Non-goals

- The React console (`web/`, ~16.7k lines of TS/TSX) is out of scope.
- No capability, tool, skill or transport is removed.
- `security.py` and `doctor.py` keep their current shape.
- No model or provider-chain change. The chain is settled.

## Rollback

Every PR is small, independently green, and merged with a merge commit — so
rollback is `git revert` of one merge, followed by a rebuild. No phase leaves
the system in a state where the previous phase cannot be reverted alone.

## Teaching

`LEARN.md`'s eight layers map nearly one-to-one onto these phases: Layer 1 is
the agent loop, Layer 5 is the guards. Each PR updates its layer in the same PR,
and the architectural reasoning is explained at the point of change rather than
saved for the end.

## Appendix — verified baseline, 2026-08-26/27

Measured, not estimated. Re-derive only if something looks wrong.

| Metric | Value |
|---|---|
| Package | 24,662 lines, 82 files |
| Growth | 19,356 (Jul 1) → 24,662 (Aug 26), +27% |
| Commit direction | 56 of last 60 grew, 4 shrank; max reduction −27 |
| Functions > 100 lines | 22 of 805 |
| Largest | `_dispatch_tool_calls` 315, `_run_loop` 271, `on_tool_call` 221, `call_llm_stream` 220, `run_output_guard` 196 |
| Guard surface | 2,797 lines across 7 modules (11%) |
| Debt markers | zero TODO/FIXME/XXX/HACK |
| Tests | 1,404 passing, 32 skipped, 70.8% coverage; ruff clean; pyright 0 |
| Skill success | 68% by run, **88% by occurrence**; retry bias ~1.5× |
| Weakest skill | `weekly-hacker-news-ai-summary`, 69% by occurrence |
| Run history | `run_history_cap = 20`, rolling |
| Memory | 29 entries, 11 edges, 22 orphans (exclude `MEMORY.md`; it holds 21 index links) |
| Prompt | 14,691 tokens average, 51% cached, 34.7:1 input:output |
| Chain | deepseek-v4-flash → ling-3.0-flash → gemini-flash-lite → gpt-oss-120b (Cerebras) |
| Tool usage | 35 of ~56 registered tools ever called |
| Transports | Discord and Telegram have zero inbound messages; Telegram carries outbound notify |

### Corrections already made — do not re-derive

- `email-event-watch` is **not** the worst skill. It is 73% by occurrence and
  improving. The earlier "40%, worst performer" reading came from retry-biased
  per-run counting.
- `gmail_search` is **working**. The "failed 3 of 5 runs" claim was quoted from a
  stale prompt snapshot inside a trace.
- Memory link counts must exclude `MEMORY.md`. Including the index makes almost
  every entry look connected.
- Use `audit_memory_links` and the other audited functions rather than ad-hoc
  scripts. Both earlier bad numbers came from hand-rolled one-liners.

### Known open items

- `c459eea`'s commit message still names a counterparty company. Removing it
  needs a history rewrite and force-push of a public `main` — owner's decision.
- Six advisory `doctor` findings at startup, all pre-existing.
- `_MODEL_PRICING_CENTS` lists deepseek at (14.0, 28.0); OpenRouter now charges
  $0.06/$0.12 per 1M. The table feeds the daily budget ceiling.
