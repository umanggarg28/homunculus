# OSS-Patterns Implementation Plan

**Date started:** 2026-06-16
**Source:** code-verified read of pi, letta, mem0, OpenManus, openclaw, NousResearch/hermes-agent
(see memory `oss-takeaways-for-homunculus` → "Code-verified read, 2026-06-16").
**Trigger:** the hallucinated morning-brief incident (skill demanded weather/calendar
tools that don't exist → the weak model fabricated them).

## Hard constraint: $5/month budget ([[feedback_monthly_budget_5_usd]])

The cost risk is **new LLM calls**, not HTTP/CPU. Rule for every phase below:
**do not add a per-turn or per-write LLM call.** Anything that needs model
judgement (commitments extraction, memory consolidation) must **piggyback an
LLM call we already make** — primarily the once-a-day reflection tick. Weather,
geocoding, validation, skill-rating, and capability-gating are all
deterministic (zero model cost). Verify spend via `/api/stats/today` after each
phase.

---

## Phase 1 — Weather tool + location config + brief grounding  *(closes the incident)*

Pattern: weather-mcp "saved location" + Home Assistant onboarded config +
Homunculus's own timezone autodetect. Weather is a **Tool**, not skill prose
(Hermes Skill-vs-Tool rule). Location is **config, never model-guessed**
([[feedback_weak_model_params]]).

- [x] **1a.** `user_location.py` — mirrors `user_tz.py`. Persists `workspace/user_location.txt`
      (`lat,lon,label`), cache + range validation, returns None (no guess) when unset.
- [x] **1b.** `/api/user-location` GET/POST (mirrors `/api/user-tz`); supports `{lat,lon,label}` or
      `{city}` (Open-Meteo geocode). `App.tsx` `UserLocationSync` captures once via
      `navigator.geolocation`. `api.ts` `userLocationGet/Set`. Typed-city fallback UI = TODO (Settings).
- [x] **1c.** `tools/weather.py` `get_weather()` — Open-Meteo forecast (no key), reads config,
      returns condition + high/low; clear "WEATHER UNAVAILABLE" when unset/failed (never guesses).
      Registered in `mcp_server`. `geocode_city()` re-exported for the API. Live-verified + 10 tests.
- [ ] **1d.** Link `morning-brief` → `skill_daily_brief`. Rewrite the skill body: commitments
      (`task_health_summary`) + system alerts + top-3 HN (Algolia) + weather (`get_weather`).
      **Drop calendar** (deferred). Declare `requires_tools`. → **moved to Phase 2** (needs the
      task↔skill binding mechanism, 2d, to link without hand-editing tasks.json).
- **Budget:** zero new LLM calls (weather + geocode are HTTP). ✓

## Phase 2 — `requires_tools` capability gating  *(Hermes; structural fix for the bug class)*

Pattern: Hermes `metadata.requires_tools` → a skill is hidden / not run when a tool it
needs is absent. Prevents a skill instructing capabilities we lack.

- [ ] **2a.** Skill frontmatter: parse `requires_tools: [..]` (extend `_parse_skill_frontmatter`).
- [ ] **2b.** `skill_validation`: validate every `requires_tools` entry exists in the live
      catalogue — at **propose** time (currently skips `known_tools`) **and** approve time.
      Extend the existing check that today only covers `states:` tools.
- [ ] **2c.** `_plan_tick`: if a linked skill's `requires_tools` are missing, **refuse to run**
      (record a clear failure) instead of injecting the playbook and letting the model improvise.
- [ ] **2d.** Skill↔task binding: on proposal approval, warn when the approved skill has **no task
      using it** (orphaned skill — the exact state that made yesterday's HN edit a no-op).
- **Budget:** zero LLM calls.

## Phase 3 — Skill feedback loop  *(harness-owned; replaces unreliable self-report)*

Pattern: Voyager objective verification + Hermes evaluate→refine. Today `rate_skill` is
**self-reported by the weak model** — unreliable. The harness already computes the
authoritative pass/fail (TaskGuard verdict).

- [ ] **3a.** Harness auto-rates the skill from the post-tick verdict (`memory.rate_skill` on the
      task's `skill`). Suppress the model's `rate_skill` during heartbeat ticks (guard no-op) so
      counts can't be double-counted/fabricated.
- [ ] **3b.** 👍/👎 on deliveries → feeds the same skill track record + the reflection digest
      (endpoint + a tactile CRT control + notification action). The cheap, high-quality human
      signal the weak model can't produce itself.
- **Budget:** zero LLM calls (rating is mechanical).

## Phase 4 — Commitments  *(OpenClaw; proactivity)*

Pattern: OpenClaw `commitments/extraction.ts` — mine conversation for follow-ups
(`agent_promise` vs `inferred_user_context`; kinds `event_check_in / deadline_check /
care_check_in / open_loop`), wired to the heartbeat.

- [ ] **4a.** Commitment store + types (kind, source, sensitivity, due, status).
- [ ] **4b.** Extraction **piggybacked on the daily reflection tick** (NOT a new per-turn call) —
      scan the day's chat log for commitments, dedup against existing.
- [ ] **4c.** Surface via existing nudge/heartbeat machinery: "you mentioned X — want a reminder?"
- **Budget:** must add ~0 extra calls — extraction rides the reflection call. Verify.

## Phase 5 — Memory consolidation  *(mem0; hygiene)*

Pattern: mem0 `DEFAULT_UPDATE_MEMORY_PROMPT` — an LLM memory manager decides
ADD / UPDATE / DELETE / NONE per fact against existing memories (dedup + decay).

- [ ] **5a.** Consolidation pass over the markdown vault: cluster near-duplicates, propose
      UPDATE/DELETE. **Runs inside the daily reflection tick** (batched), not per-write.
- [ ] **5b.** Human-gated for DELETE (reuse the proposal/approval gate) to avoid silent loss.
- **Budget:** rides the reflection call. Verify.

## Phase 6 — Weigh (decide with Umang, not auto-build)

- [ ] **6a. `active-memory`** (OpenClaw): a blocking pre-reply memory sub-agent. **Directly
      contradicts** our deliberate Letta-style `recall()` move, and is the **one expensive
      pattern** (an LLM call before every reply). If built: opt-in, OFF by default, cheap model.
- [ ] **6b. `context-engine` slot** (OpenClaw): compaction/assembly as a swappable engine vs inline
      `_maybe_compact`. Low urgency, pure refactor.

---

## Progress log
- 2026-06-16: plan written. Starting Phase 1.
