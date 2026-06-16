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

- [x] **2a.** `requires_tools` parsed (`skills.load_skill_requires_tools` + `skill_validation`).
- [x] **2b.** `skill_validation` validates `requires_tools` against the live catalogue; approval
      already passes `known_tools` so the gate is authoritative at apply time. (Propose-time stays
      catalogue-free by existing design — `authoring.py` documents why; approval re-validates.)
- [x] **2c.** `_plan_tick` capability-gate: a skill whose `requires_tools` are missing gets a
      BLOCKED directive (→ `record_failure`) instead of its playbook — no improvisation. Fails open
      if the catalogue can't be read. (`heartbeat._known_tool_names`; 2 tests.)
- [x] **2d.** Binding: `PATCH /api/tasks/{id}` accepts `skill` (validated to exist) so a task is
      linked via the API, not a tasks.json hand-edit. Approval returns an **orphan warning** when an
      approved skill has no task using it.
- **Budget:** zero LLM calls. ✓ — 1d (link + skill rewrite) happens post-deploy via the API.

## Phase 3 — Skill feedback loop  *(harness-owned; replaces unreliable self-report)*

Pattern: Voyager objective verification + Hermes evaluate→refine. Today `rate_skill` is
**self-reported by the weak model** — unreliable. The harness already computes the
authoritative pass/fail (TaskGuard verdict).

- [x] **3a.** Harness auto-rates the skill from the post-tick verdict (`_rate_task_skill` →
      `memory.rate_skill` on success / explicit record_failure). Model's `rate_skill` suppressed
      during ticks via the TaskGuard. 6 tests. Conftest now ships a canonical `tools.notify` stub.
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

## Phase 7 — Multi-channel delivery (OpenClaw channel-router)  *(NEW, prompted by the India Telegram block)*

Telegram is gov-blocked in India until ~2026-06-22. Make delivery channel-agnostic
and ADDITIVE — Telegram stays and auto-resumes; new channels deliver meanwhile.
Reachability principle: relay channels (Telegram/Discord) need only OUTBOUND net
and work anywhere; Web Push needs the server reachable over HTTPS (→ Tailscale).

- [x] **7a. notify fan-out + web-always-record (foundation).** Shipped #198. Live-verified. `notify()` records to the
      `_notifications.jsonl` feed FIRST (the web app is an always-on channel — a delivery is
      never lost even with every push channel down), then fans out to each configured channel.
      Success if recorded OR any channel delivered — a blocked channel must NOT fail the task for
      days. `deliver(text)` shared by notify + heartbeat's autonomous fallback.
- [x] **7b. Discord sender** — shipped #198, live-verified (pushed via discord). (REST `POST /channels/{id}/messages`, Bot token) wired into the
      fan-out. No new dep (httpx). Needs `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID`. One-way push
      (briefs/quiz/leetcode) works as soon as those are set. Works from a purely-local deploy.
- [x] **7c. Discord listener** — shipped #198, live (bot locked to user, replies route to chat). (`transports/discord.py`, discord.py gateway) for REPLIES →
      `agent.chat` + quiz grading, mirroring `transports/telegram.py`. New compose service +
      `discord.py` dep. Makes it two-way.
- [ ] **7d. Tailscale (infra, user-run)** — `tailscale serve` exposes the local app over HTTPS to
      the user's own devices (private, free). The prerequisite for Web Push reachability. Document
      + a helper; not repo code.
- [ ] **7e. Web Push (PWA)** — add a `push` handler to `web/public/sw.js`, generate VAPID keys,
      `/api/push/subscribe` + a subscription store, a push sender in the fan-out, and frontend
      subscribe-on-permission. iOS 16.4+ supports it for home-screen PWAs (no App Store / Apple
      dev account) — gated only on the HTTPS reachability from 7d.
- **Budget:** Discord/Web Push are HTTP (zero LLM cost). Tailscale free. ✓

---

## Progress log
- 2026-06-16: plan written. Phases 1–3 shipped (#195/#196/#197 + #194). Brief grounded & verified.
- 2026-06-17: India blocked Telegram (→ June 22). Added Phase 7 (multi-channel). Discord shipped
  (#198) + configured + live-verified (push + two-way, locked to user). Web feed fallback live.
  Remaining: 7d Tailscale + 7e Web Push. Backlog: Phase 3 (skill feedback loop), 4, 5.
