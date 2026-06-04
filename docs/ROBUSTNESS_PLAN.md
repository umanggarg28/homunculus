# Homunculus — Agent Robustness Refactor

**Date:** 2026-06-04
**Branch:** `feat/agent-robustness`
**Scope:** 6 changes, ≈1 week of focused work
**Goal:** eliminate the recurring failure shapes we've patched piecewise, and make future iterations of the agent inherently more robust.

---

## 1. Why we're not robust despite many iterations

Every reliability fix in this repo so far has been *additive*: we patch the
specific failure we just saw, then the next failure exposes a different shape.
What's missing is the structural work that makes robustness *emergent*.

### 30-day failure histogram (from `_events.jsonl`)

| Pattern | Count | Root cause |
|---|---:|---|
| `provider_exhaustion` | 9 | All providers 429-cooled simultaneously |
| `agent_didnt_complete` | 2 | Context bloat → fallback path |
| `api_404_model_gone` | 1 | Stale model ID in config |

### Today's failure (2026-06-04 03:30 UTC LeetCode tick)

Reconstructed from the SSE event stream:

```
06:20:50  recall      skill_deliver_daily_leetcode  ← good
06:20:55  read_file   tracker                       ← good
06:20:59  python      manipulate list               ← good
06:21:06  read_file   tracker (SAME args!)          ← loop start
06:21:09  read_file   tracker (3rd time, SAME)      ← stuck-loop guard fires
06:21:13  web_search  LeetCode Top 150              ← recovers
06:21:18  web_fetch   leetcode.com → 403            ← blocked
06:21:21  web_fetch   github mirror → 200           ← gets full README (≈50KB)
06:21:36  llm_call    ???                           ← 38 s later…
06:22:14  assistant_reply  "I'm not sure how to respond — could you rephrase?"
```

The agent had every piece of data it needed by 06:21:24. But by then its
conversation history contained the full skill text, the tracker file three
times (twice wasted), the python output, the stuck-loop warning, the search
result, the 403 error, **and the entire LeetCode-150 GitHub mirror README
(≈50 KB of markdown)**. The final LLM call ran for 38 seconds and produced
empty content. **Context bloat killed the answer.**

### What we already have (defensive layers in place)

We're not without guards — these all exist and have caught real bugs:

- Stuck-loop detector (`output_guard` returned `STUCK_LOOP` for 3× read_file today)
- TaskGuard for success_criteria (`heartbeat.py:TaskGuard`)
- Output guard for hallucination patterns (`core.py:_output_guard`)
- Provider fallback chain (`core.py:262–265`)
- Post-success completion check (`heartbeat.py`, added 2026-06-04)
- Startup cleanup for stuck executing flags (`heartbeat.py:main()`)
- In-tick stale-executing recovery (`heartbeat.py:tick()`, added 2026-06-04)

What's **structurally missing** is the harness-level engineering that robust
OSS agents do.

---

## 2. What OSS robust agents do that we don't

Mapped to source files where we can study the patterns ourselves.

| Pattern | OSS source | What it solves |
|---|---|---|
| **Tool result trimming** | Pi `packages/coding-agent/src/core/tool-result-truncator.ts`; Cline does this too | Today's bug shape — 50 KB web_fetch dumped raw into history kills the final LLM call. |
| **Config-hook agent loop** | Pi `packages/agent/src/agent-loop.ts` (`transformContext`, `prepareNextTurn`, `shouldStopAfterTurn`) | Lets the harness inject reminders, summarise old turns, exit cleanly mid-loop. Today we hit `MAX_TURNS=20` and bail with a fallback string instead. |
| **Archival memory offload** | Letta / MemGPT `letta/agents/memgpt_agent.py` (`archival_memory_insert`, `archival_memory_search`) | Tool results live OUTSIDE the conversation; agent retrieves on demand. Context stays bounded forever. |
| **"Did the right thing happen?" output validator** | Pi `coding-agent/src/core/output-guard.ts` | We have criteria validation only *after* `complete_task` is invoked. Doesn't catch the case where `complete_task` is never called at all. |
| **Per-skill failure feedback** | Hermes Nous Research, evaluate→refine→retrieve loop | Failed runs feed back into the skill description so the next attempt avoids the same pitfall. Our skills are write-once. |
| **Schema-validated tool args** | Pi, OpenClaw (Zod schemas at LLM↔code boundary) | Catches malformed tool calls (e.g. the 2025-05-25 `tool_use_failed`) and retries with a structured error rather than crashing. |

Source notes in [`reference_oss_agents.md`](../../.claude/projects/.../memory/reference_oss_agents.md) (auto-memory).

---

## 3. Roadmap — six changes in priority order

Each item is independently shippable. Items 1–4 are tactical (single file each);
items 5–6 are structural and pay long-term dividends.

### Item 1 · Tool result trimming  *(½ day)*

**Problem:** raw `tool_result` content goes straight into `self.history` —
`web_fetch` results can be 50 KB+.

**Change:** `core.py:_run_loop` — apply a hard char cap before
`self.history.append({"role": "tool", "content": result})`.

```python
TOOL_RESULT_HARD_CAP = 6000  # ~1500 tokens

def _trim_for_history(name: str, result: str) -> str:
    if not isinstance(result, str) or len(result) <= TOOL_RESULT_HARD_CAP:
        return result
    head = result[:TOOL_RESULT_HARD_CAP - 200]
    trimmed = len(result) - (TOOL_RESULT_HARD_CAP - 200)
    return (
        f"{head}\n\n"
        f"[+{trimmed:,} chars trimmed by harness — re-call with a more "
        f"targeted query/path if needed, or use recall() / search_files()]"
    )
```

**Don't trim:** `tool_result` events emitted to `_events.jsonl` keep full
content (the Traces UI already handles truncation). Only trim history.

**Tests:** mock a 50 KB tool result; assert history entry ≤ cap + N hint
chars; assert `[+chars trimmed]` suffix is present.

**Files:** `core.py` (~30 LoC), `tests/test_core.py` (new test).

---

### Item 2 · MAX_TURNS budget nudge  *(½ day)*

**Problem:** when the agent hits `MAX_TURNS=20`, it bails with
`"(hit MAX_TURNS without a final answer)"`. No graceful "you're running out
of budget — finish up" prompt.

**Change:** `core.py:_run_loop` — at `iter == MAX_TURNS - 2`, inject a
synthetic user message:

```python
if turn_idx == MAX_TURNS - 2:
    self.history.append({
        "role": "user",
        "content": (
            "Heads-up from the harness: you have 2 iterations left. "
            "If a due task is still active, call complete_task() now "
            "with what you have. Otherwise, send a final reply."
        ),
    })
```

**Tests:** force a loop with a slow tool; assert the nudge appears at iter 18;
assert the agent calls `complete_task` in iter 19.

**Files:** `core.py` (~15 LoC), `tests/test_max_turns_nudge.py` (new).

---

### Item 3 · Read-file caching within a tick  *(½ day)*

**Problem:** the stuck-loop guard catches duplicate `read_file` calls but
returns an unhelpful `STUCK_LOOP: …` error. The agent then makes a new round
of API calls to recover. Wasted iterations + tokens.

**Change:** `tools/__init__.py` (or wherever the stuck-loop guard lives) —
maintain a per-tick `{call_key → result}` cache. On duplicate, return the
*cached* result with a hint:

```python
if call_key in self._tick_cache:
    return (
        f"(harness-cached result from earlier this tick)\n\n"
        f"{self._tick_cache[call_key]}\n\n"
        f"[Hint: you already called {name} with these arguments. "
        f"No need to re-call — proceed with the next step.]"
    )
```

**Tests:** call `read_file` twice with the same path in one tick; assert
second call returns cached + hint, doesn't hit disk.

**Files:** `core.py` or `tools/__init__.py` (~25 LoC), test (~30 LoC).

---

### Item 4 · TaskGuard "completion required" check  *(1 day)*

**Problem:** TaskGuard validates `success_criteria` only when
`complete_task()` is invoked. If the agent loop ends without ever calling
`complete_task`, no validation runs — the task stays active, no failure is
recorded. (Mitigated this morning by the heartbeat post-success check, but
that's a post-hoc failure record, not a prevention.)

**Change:** `heartbeat.py:TaskGuard` — track which tasks have *not* called
`complete_task`. Before yielding the final assistant turn:

```python
def expected_remaining(self) -> list[str]:
    """Task IDs that were due but never had complete_task called."""
    return [
        tid for tid in self._criteria_by_task
        if tid not in self._completed_tasks
    ]
```

`heartbeat.tick()` — at iter `MAX_TURNS - 1` (after the budget nudge from
item 2), if `guard.expected_remaining()` is non-empty, inject:

```python
self.history.append({
    "role": "user",
    "content": (
        f"BLOCKING: the task '{tid}' was due but has not been completed. "
        f"You must call complete_task('{tid}', result=...) now or "
        f"record_failure('{tid}', reason=...) if it cannot be done."
    ),
})
```

**Tests:** mock an agent that calls `notify()` but never `complete_task()`;
assert the forced message is injected; assert `complete_task` is called
afterwards.

**Files:** `heartbeat.py:TaskGuard` (~40 LoC), `core.py:_run_loop` (small),
test (~50 LoC).

---

### Item 5 · Pi-style `_run_loop` refactor with hooks  *(2 days)*

**Problem:** `chat()` and `chat_stream()` are two near-identical 200-line
functions, both calling `_run_loop()` which hard-codes every harness concern
inline. Adding a new behaviour (like items 2 and 4 above) means editing the
loop directly. Pi's loop accepts hooks; ours doesn't.

**Change:** refactor `core.py:_run_loop` to accept a config dict:

```python
@dataclass
class LoopConfig:
    transform_context: Callable[[list[dict]], list[dict]] | None = None
    prepare_next_turn: Callable[[int, list[dict]], list[dict]] | None = None
    should_stop_after_turn: Callable[[int, dict, list[dict]], bool] | None = None
    on_tool_call: Callable[[str, dict], str | None] | None = None  # existing hook
    on_tool_result: Callable[[str, str], str] | None = None         # NEW — for trimming
```

Existing hard-coded behaviour moves into default config hooks. New behaviours
(item 2 nudge, item 4 completion check) become *config callbacks* instead of
inline edits. `chat()` and `chat_stream()` become 30-line wrappers that
construct a `LoopConfig` and yield from `_run_loop(cfg)`.

**Why this is non-trivial:** streaming and non-streaming paths currently
diverge in tool-call handling, output guard application, and history
mutation. A clean refactor needs each hook to work in both modes.

**Migration:** preserve current behaviour exactly — every existing inline
check becomes a default-on hook. Tests must pass unchanged before any new
hooks land.

**Files:** `core.py` (~300 LoC of refactor), comprehensive
`tests/test_loop_hooks.py` (~200 LoC).

---

### Item 6 · Letta-style archival memory for tool results  *(3 days)*

**Problem:** even with item 1's trimming, a long-running session accumulates
tool results in history forever. Compaction (`_maybe_compact`) summarises
them, but the summarisation itself costs tokens and loses fidelity. For very
long sessions or many heartbeat ticks, this still grows unboundedly.

**Change:** add two new tools to `tools/__init__.py`:

```python
def archival_memory_insert(content: str, tags: list[str] = []) -> str:
    """Save content to archival storage; returns a reference token."""

def archival_memory_search(query: str, k: int = 5) -> str:
    """Retrieve top-k matching archival entries by semantic similarity."""
```

When `_run_loop` sees a `tool_result` larger than some threshold, the
`on_tool_result` hook (added in item 5) auto-inserts the content into
archival memory and replaces the history entry with:

```
[Large result stored in archival memory.
 Token: arch_2026-06-04T06_21_24_a3f9
 Preview: <first 500 chars>
 Retrieve full content via archival_memory_search(query="…")]
```

Archival storage = SQLite + Gemini embeddings (we already use these in
`memory.py`). Conversation history stays small permanently.

**Why this is hard:** designing the retrieval query semantics. Letta uses
plain keyword + embedding similarity. We should mirror that exactly — don't
reinvent.

**Files:** `tools/__init__.py` (new tools, ~80 LoC), `memory.py` (archival
table + insert/search, ~120 LoC), `core.py` (auto-insert hook in
`_run_loop`, ~30 LoC), tests (~150 LoC).

---

## 4. Test strategy

Each item ships with at least one regression test that **fails before the
change and passes after.** Test files map 1:1 to items:

```
tests/
  test_tool_result_trimming.py    (item 1)
  test_max_turns_nudge.py         (item 2)
  test_tick_tool_cache.py         (item 3)
  test_task_guard_completion.py   (item 4)
  test_loop_hooks.py              (item 5 — large, covers all hooks)
  test_archival_memory.py         (item 6)
```

For items 5–6, also add an **integration test** that replays today's
LeetCode-tick event sequence (recorded from `_events.jsonl`) and asserts the
agent successfully calls `notify()` and `complete_task()`.

---

## 5. Rollout order

Each item lands as its own PR off `feat/agent-robustness`. Order is chosen so
later items can rely on earlier hooks without re-plumbing:

1. **Items 1, 2, 3** — tactical bundle. Each touches one file each. Land
   together in one PR (~½ day total) since they're small and additive.
2. **Item 4** — TaskGuard extension. Independent PR.
3. **Item 5** — `_run_loop` refactor. Independent PR. Must pass entire
   existing test suite unchanged before any hook-based item lands on top.
4. **Item 6** — archival memory. Depends on item 5's `on_tool_result` hook.

**Estimated total: 5 working days + 1 day integration buffer.**

---

## 6. Acceptance criteria

After all six items ship, the following must hold:

1. **The LeetCode tick that failed today reproduces successfully.**
   Replay-test the event sequence; assert `notify()` and `complete_task()`
   are called within `MAX_TURNS`.
2. **Conversation history token count grows ≤ linearly in time for a
   long-running heartbeat session.** Currently it grows superlinearly
   because every tick re-reads memory + writes a tool result.
3. **No regression in existing test suite.**
4. **`_events.jsonl` shows ≤ 1 `agent_didnt_complete` event per week** in
   normal operation (vs 2/month currently).
5. **No new `provider_exhaustion` events** caused by wasted retries on
   stuck loops or context-bloat-induced empty replies. (Won't eliminate
   provider exhaustion entirely — that's an upstream issue — but should
   reduce harness-caused exhaustion to ~0.)

---

## 7. Out of scope (deferred)

The following are robustness improvements but not part of this refactor:

- **Provider chain resilience** — better retry/backoff, per-provider quota
  tracking. Worth doing separately; not tied to harness architecture.
- **Hermes-style skill refinement loop** — auto-update skill_*.md files
  based on failed runs. Useful but orthogonal; tackle after item 5 ships
  (it benefits from the same hook infrastructure).
- **Schema-validated tool arguments** (OpenClaw pattern) — we have
  `tools.SCHEMAS` (JSON Schema) but don't validate at runtime. Worth doing
  but not on the critical path for the failure shapes we're seeing.

These get their own follow-up plans once this refactor lands.
