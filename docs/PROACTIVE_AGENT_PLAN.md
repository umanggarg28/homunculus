# Homunculus — Proactive Agent Plan

**Date:** 2026-07-01
**Theme:** Turn the heartbeat from a *scheduler that fires fixed skills* into a
*proactive agent that maintains a model of the user and surfaces only what
matters*. Close the gap between "glorified cron" and "agent."

---

## Why this plan exists (the evidence)

Measured against the live system on 2026-07-01, not vibes:

1. **It mostly talks to itself.** Of the last ~6 days of `user_message`
   events, all but one were the system prompting *itself* on a schedule
   (`heartbeat tick` / `REFLECTION tick`). One organic human turn in the window.
2. **Its memory is about itself.** 3 `user_*` facts (name, language, editor)
   plus dark-mode + a standup note ≈ 5 genuinely personal facts, against ~19
   `feedback_*`/`reflection_*` operational self-logs. It has barely any model of
   the user, so it has nothing to be proactive *about*.
3. **Its self-concept of "useful" = its own schedule.** Asked live "what's the
   single most useful thing you could do for me this week?" it answered "keep
   delivering a LeetCode problem each morning, as defined in the playbook."

A 4-probe live stress test (isolated workspace, notify blanked) showed the
*machinery generalizes* — novel HN research, honest clarification, and a
`watch` setup all worked. So capability is not the gap. **Judgment and a model
of the user are the gap.** It has hands; it doesn't know what to reach for or
why unless a schedule tells it. One probe also exposed a capability-honesty gap:
asked to book a restaurant (no booking tool exists), it asked for the city
rather than admitting it cannot book.

---

## OSS grounding (real code, convergence — not blog listicles)

Re-confirmed against the projects in [[oss-agents]] / [[oss-takeaways-for-homunculus]]
(code-verified 2026-06-16/17) plus a 2026-07-01 source check:

- **OpenClaw `commitments/`** — mines conversation for commitments
  (`event_check_in` / `deadline_check` / `open_loop` / `care_check_in`), tagged
  by source (`agent_promise` vs `inferred_user_context`) and sensitivity, then
  *wired into the heartbeat* so the agent notices follow-ups instead of only
  running user-created tasks. The reference proactivity pattern.
- **Letta sleep-time agents** (docs.letta.com/guides/agents/architectures/sleeptime)
  — a background agent that *shares the primary agent's memory* and updates it
  asynchronously from conversation history. "Dream"/"subconscious observer"
  variants review recent conversations and write lessons + proactive guidance.
- **Hermes** — memory split into **skill library + user model + episodic logs**
  (the explicit "user model" tier we lack), and an open issue (#553) to add a
  Letta-style subconscious observer for cross-session pattern detection +
  proactive guidance. Convergence with Letta is what makes this trustworthy.
- **mem0** consolidation (`DEFAULT_UPDATE_MEMORY_PROMPT`) — an LLM memory
  manager decides ADD / UPDATE / DELETE / NONE per new fact vs existing memory:
  dedup + decay. Fixes the monotonically-growing-vault hygiene gap.
- **Hermes `requires_tools`** — capability gating; a skill/claim is hidden or
  refused when the backing tool is absent. The structural fix for the
  capability-honesty gap.

**Key reframe:** Homunculus's existing daily REFLECTION tick *is* a primitive
sleep-time agent. We don't add a subsystem — we point the one we have at the
user model + proactivity instead of self-logs.

**Anti-patterns to avoid** (from prior survey): don't build a heavy skill-library
subsystem (Hermes oversells prompt-templates-with-a-counter); don't adopt
Mem0/Cognee infra until the markdown vault is actually slow; keep proactivity
*deterministic-first* so the $5/mo budget survives.

---

## Constraints (non-negotiable)

- **Budget $5/mo.** Proactivity must be cheap: a deterministic relevance filter
  runs *before* any LLM call. No new always-on model loops.
- **Weak model.** Every new model-touch gets deterministic scaffolding +
  capability gating ([[feedback_weak_model_params]]).
- **No hardcoding / no manual state patches** — fix behavior structurally.
- **PR per concern**, each green; LEARN.md updated in the same PR; real
  end-to-end regression (live model+tools or container smoke), not just pytest.

---

## Phase 0 — Wider stress baseline *(measure before building)*

Run a 12–15 probe off-script battery in an isolated workspace (real model+tools,
notify blanked, temp memory/tasks/events) across: multi-step research, ambiguous
requests, refusal/honesty (capabilities it lacks), `watch`/monitoring,
memory-synthesis, code/sandbox, multi-turn follow-through, scheduling edge cases.
Record per-probe: tools used, honesty, hallucination, success. This is the
baseline "next level" is measured against, and it maps the real break-points
so later phases target them.

**Deliverable:** `docs/STRESS_BASELINE_2026_07.md` (results table + verdicts).
No production code change. **DONE 2026-07-01** — 12 probes; surfaced 3 real
defects (A fabrication, B flailing, C over-claim), motivating Phase 0.5.

---

## Phase 0.5 — Hardening *(production-grade gate; before proactivity)*

The baseline proved proactivity must not be built on an ungrounded, over-claiming
base — it would amplify the failure. Fix the three confirmed defects first.

- **A. Chat-reply grounding.** Extend the URL-grounding gate (today wired only to
  the `notify`/delivery boundary) to chat replies: flag/strip ungrounded links,
  mark unverifiable quantitative claims. Ensure `†`-citation stripping runs on the
  reply path, not only at transport. Grounded in the Hermes "verify every
  citation / mark unverifiable" pattern already used for news.
- **B. Clarify-before-act.** On low-specificity input with no actionable referent,
  ask one clarifying question instead of launching tool work. The visible
  **plan/checklist** (below) is the structural enforcement: no execution before a
  plan exists, and an un-plannable request yields a question.
- **C. Capability honesty.** Extend Hermes `requires_tools`-style gating to the
  reply path so the agent declares boundaries rather than over-promising
  (the "skip public holidays" / restaurant-booking class).

**Plan/checklist surface (from `agents/1_foundations/5_extra.ipynb`).** Two tools
— `plan_steps(steps[])` and `complete_step(index, note)` — let the agent decompose
a task into a visible todo list and check items off as it works, rendered live in
the chat UI (strike-through on completion, à la Claude's own task view). Value:
(1) makes multi-step agent reasoning *visible* — the recruiter-facing "this is a
real agent" signal; (2) forces plan-before-act, the structural fix for defect B;
(3) legible progress on long tasks. **Budget guard:** gated to genuinely
multi-step tasks (a complexity heuristic) — never engaged for "what's the
weather," or it doubles tool round-trips on a weak model under the $5/mo cap.

**Files:** `core.py`/`output_guard.py` (grounding + clarify gate), new plan tools
in `tools/`, web UI checklist component, capability-gate in `skill_validation.py`
+ reply path. **Test:** ungrounded URL in a chat reply is flagged; "Set it up"
yields a question not 8 web calls; an un-tooled claim is refused; plan tools
render + gate correctly. Live: re-run the failing baseline probes (#1, #2, #9)
and confirm corrected behavior.

---

## Phase 1 — Model-of-you memory *(the fuel; prerequisite)*

Turn the daily REFLECTION tick into a real sleep-time pass that builds a model
of the user instead of logging itself.

- **User-model tier (Hermes).** New memory `type: user_model` — goals, active
  projects, deadlines, preferences, people. Reflection extracts these from the
  day's *real* conversations (not from its own delivery logs).
- **Consolidation (mem0).** Before writing, an ADD/UPDATE/DELETE/NONE pass
  against existing user-model memories: dedup, supersede, decay stale facts.
  Deterministic guard rails on the model's decision (never delete a `feedback`
  the user gave; cap deletes/turn).
- **Core block (Letta).** A small always-in-context `_core.md` (≤1KB) holding
  the current user model + active focus, injected every turn. Edited only by
  the consolidation pass, reviewable/deletable in the memory UI.

**Files:** `heartbeat.py` (`_run_reflection_or_idle` → user-model extraction),
`memory.py` (consolidation API, `user_model` type, core-block read/write),
`tools/memory_tools.py`, bootstrap script for the `_core.md` template.
**Test:** reflection over a synthetic day produces correct ADD/UPDATE/DELETE;
core block stays ≤1KB; protected types never deleted. Live: one real reflection
run writes user facts, not self-logs.

---

## Phase 2 — Proactive judgment loop *(the cron→agent flip)*

- **Commitment extraction (OpenClaw).** A pass (folded into the reflection /
  sleep-time tick — no new always-on loop) mines conversations for commitments:
  `deadline_check` / `event_check_in` / `open_loop` / `care_check_in`, each with
  a due/check time and source (`agent_promise` vs `inferred_user_context`).
  Stored alongside tasks.
- **Relevance bar (deterministic-first).** Each heartbeat scans *signals* —
  commitments due, `watch` diffs, GitHub state, calendar (Phase 3), user-model
  deadlines — and a **deterministic filter** decides what's worth surfacing
  *before* any LLM spend. Only survivors get a single model call to compose the
  message. Threshold + per-day proactive-message cap (budget + anti-nag).
- **Visibility (the recruiter story).** A `proactive_surface` event + a UI
  treatment so "it told me unprompted that my watched PR got a failing review"
  is *visible*, not buried. This is what makes the project read as a big deal.

**Files:** `heartbeat.py` (signal scan + relevance gate), new
`commitments.py` (extraction + store, mirrors `tasks.py` shape),
`tools/notify.py` (proactive channel + cap), web UI surface.
**Test:** commitment extraction on fixtures; relevance gate admits/rejects
correctly; budget cap enforced; no proactive message without a crossed signal.
Live: a seeded commitment + a real watched-page change each produce one
unprompted, grounded message.

---

## Phase 3 — Give it hands + honesty *(read-only actions)*

- **Calendar + Gmail (read-only).** Mount via MCP (roadmap NEW.1): scopes
  `calendar.events.readonly` + `gmail.readonly`, OAuth refresh-token persisted
  0600/gitignored, no write scopes. Unlocks "your actual day" in the brief and
  feeds the proactive loop (real deadlines/events).
- **Capability honesty (Hermes `requires_tools`).** Extend gating so the agent
  declares boundaries — "I can't book a table, but I'll find 3 options and draft
  the reservation email" — instead of implying capabilities it lacks (fixes the
  P2 gap). Validate at propose/approve and in the reply path.
- **Extend `watch`** to the signal set the proactive loop consumes.

**Files:** `tools/google_calendar.py`, `tools/google_gmail.py`,
`tools/__init__.py`, `skill_validation.py` / reply-path gating, bootstrap for
`skill_inbox_triage.md`.
**Test:** read-only scope enforced; OAuth refresh; capability-gate refuses a
skill/claim whose tool is absent. Live: brief reflects a real calendar event;
agent honestly declines an un-tooled request.

---

## Execution order & DoD

Order: **0 → 0.5 → 1 → 2 → 3.** Hardening (0.5) lands before proactivity so it
isn't built on an ungrounded base. Phase 1 is the prerequisite for 2 (proactivity needs
the user model as fuel); 3 enriches the signal set. Each phase = one or more PRs,
each green (822+ tests), LEARN.md updated in-PR, a real end-to-end regression run,
roadmap entry ticked with PR number. Budget impact estimated and logged per phase.
