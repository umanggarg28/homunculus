# Learning log

A running record of the from-scratch teaching pass over this codebase: one section
per level, plus worked investigations as they happen. `LEARN.md` is the polished
tutorial narrative; this is the working notebook — rougher, in the order things were
actually learned, and it keeps the real incidents that motivated each idea.

Companion doc: `docs/CODE_REVIEW_2026_08_18.md` holds the 45 findings; each level's
exercise fixes one of them.

---

## Level 1 — The agent loop

### The whole idea

An LLM API is a pure function: messages in, message out. It cannot *do* anything.
What makes it agentic is that its reply comes back in one of two shapes — prose for
the user, or a request that you run a function and report back. Two shapes means you
need a loop, and that loop is the entire concept:

```python
def chat(user_message, tools, schemas):
    history = [{"role": "system", "content": SYSTEM_PROMPT},
               {"role": "user", "content": user_message}]

    for _ in range(MAX_TURNS):                        # 1. bounded, always
        reply = call_llm(history, schemas)            # 2. one round-trip
        history.append(reply)

        if not reply.get("tool_calls"):               # 3. prose → done
            return reply["content"]

        for call in reply["tool_calls"]:              # 4. run what it asked for
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            result = tools[name](**args)
            history.append({"role": "tool",
                            "tool_call_id": call["id"],
                            "content": str(result)})
        # 5. loop: the model sees the results and decides again
```

Point 5 is the one to sit with. You never tell the model what to do next. You hand it
results and it re-decides from scratch every iteration. The "reasoning" is the
sequence of decisions *across* iterations, not anything inside one call.

Five details are load-bearing — get them wrong and the loop breaks rather than
degrades:

1. **`for` with a bound, not `while True`.** A model that keeps calling tools will
   keep calling tools. The bound is all that stands between you and an unbounded bill.
2. **`history` accumulates everything**, the model's own replies included. The model
   is stateless; the list *is* this turn's memory.
3. **The exit condition is "no tool calls"**, not a content check — a reply can carry
   both content and tool calls.
4. **`tool_call_id` must be echoed back.** The API rejects a `tool` message that
   doesn't pair with a call it made. This is why compaction can't cut just anywhere.
5. **Tool results go in as `role: "tool"`**, not as user messages. Providers treat the
   two differently.

### Why the real one is 2247 lines

Not because the idea grew. Because each row below is a thing that actually happens,
and each costs something specific when ignored. **A production agent loop is the naive
loop plus one mitigation per way a model or provider misbehaves.**

| The naive loop's problem | What it costs | Where it lives in `core.py` |
|---|---|---|
| Model answers in prose when a tool call was needed | A heartbeat "delivers" a message that was never sent | `_loop_personality` → `tool_choice` |
| Provider ignores `tool_choice` entirely | Same, silently | `_handle_tool_choice_violation` (capped at 2 retries) |
| Provider ignores a *forced specific* tool | A state machine skips a step | wrong-forced-tool detector, rolls `state_idx` back |
| History grows every iteration | At iter 15 you re-send 90K of stale payloads *per call* | `_evict_prior_tool_results(keep_recent=2)` |
| One `web_fetch` dumps 50KB into history | No budget left for the call that would use it | `_trim_tool_result_for_history` + `_PER_TOOL_RESULT_CAPS` |
| Model re-calls a read tool with identical args | Wasted round-trips, then a real stall | per-turn cache → `STUCK_LOOP` at 3 |
| The task drifts out of attention mid-loop | Model forgets what it was doing | goal re-injection at `max_turns // 2` |
| Loop hits the cap mid-task | Task stuck "executing" forever | budget nudge at `max_turns - 2` |
| Work done but `tool_choice=required` keeps prodding | 5+ wasted LLM calls per successful tick | `expected_completions` early exit |
| Prompt prefix changes every turn | Provider cache never hits (~40% → 70-80% swing) | `_current_system_prompt`'s stable/volatile split |
| Streaming needs its own loop | Two loops that drift apart | one `_run_loop` generator; `chat()` is `"".join(...)` over it |

Read `_run_loop` (`core.py:1754`) with that framing. It is a thin orchestrator —
prepare → for each iteration { inject → call → check violation → dispatch-or-finalize }
→ fallback — and every phase is a named method defending one row of that table.

### Exercise

**A.** Rebuild the 25-line loop in a scratch dir against the real API with two tools.
Then break it deliberately: drop `MAX_TURNS` and give it an impossible task; drop
`tool_call_id`; feed it a 100KB file. Meet each failure mode yourself.

**B.** Delete `_clarify_before_act` (`core.py:523`) — a regex weak-model workaround
from the gpt-oss era — and its test. Removing a mitigation means arguing it is no
longer needed, which is the harder judgment. Also fix the module docstring's claim
that the agent is "About 120 lines."

---

## Investigation — "why is the budget exhausted?" (2026-08-18)

A worked example of reading the traces, and the best kind of bug: the system was
behaving exactly as designed and the design was wrong.

### Symptom

Every paid model blocked; `budget_blocked` events every 30s all day.

### Method (this is the transferable part)

1. **Find the ceiling, then the spend.** `HOMUNCULUS_DAILY_BUDGET_USD=0.30`.
   Aggregate `llm_call` events since the window opened, using the app's *own*
   `_record_cost_cents` rather than a reimplementation — otherwise you are testing
   your arithmetic, not the system's.
2. **Get the window right.** `_budget_cutoff_utc()` is the user's **local** midnight
   in UTC, so "today" for an IST user starts at 18:30Z *yesterday*. My first pass used
   UTC midnight, found zero calls, and would have concluded "nothing ran today" —
   wrong, and the failure mode is always the same: a filter that quietly excludes the
   window you care about.
3. **Group before narrating.** By model, by service, by hour.

### What the numbers said

| model | calls | charged | actual | over |
|---|---|---|---|---|
| `gemini-flash-lite-latest` | 7 | 20.76c | 0.83c | **25x** |
| `gpt-oss-120b` (Cerebras) | 1 | 4.35c | 0.07c | **66x** |
| `deepseek/deepseek-v4-flash-0731` | 46 | 6.06c | 6.06c | correct |

31.16c against a 30c ceiling. **Real spend ~7c — 23% of budget.** Seven calls, 67% of
the day's money, and 115 output tokens between them.

### Root cause

`_pricing_for()` falls back to `_DEFAULT_PAID_PRICING_CENTS` (frontier rates) for any
model not in `_MODEL_PRICING_CENTS`. Two of the three configured fallback ids were
missing. Note *why* they were missing: **the same model through two providers is two
ids** — Cerebras answers as `gpt-oss-120b`, OpenRouter as `openai/gpt-oss-120b`. Only
the second was listed.

The lesson is not "add the models." It is about the shape of the mistake:

> A safe default for the case you were thinking about can be a dangerous default for
> the case you weren't. Fail-closed pricing is *correct* for a model someone swaps in
> at runtime via `/use` — an unknown paid model must be counted at worst case rather
> than silently as free. It is wrong for a model this deployment configures on
> purpose. Nothing tied the chain config to the pricing table, so the two drifted.

The fix is therefore not the data edit, it is the **link**: a test that reads the live
env and asserts every id the chain can route to is priced. The data edit alone would
have re-broken on the next fallback added.

### Second finding, from the same trail

Why was a *fallback* serving those calls at all? The traces could not say. Only the
429 path emitted `provider_cooled`; a connection error or transient status cooled the
primary in silence. So the log showed gemini answering seven consecutive calls with
nothing recording that deepseek had been benched.

> A silent failure is indistinguishable from a choice. If the harness benches a
> provider, that must be an event — otherwise the cost lands on the budget with no
> explanation attached, and diagnosis becomes reconstruction.

Both cooling paths now emit.

### Two things worth stealing

**Retroactive by construction.** Cost is *derived* from the pricing table when the log
is read, never stored on the event. Fixing the table re-priced the existing records
and lifted the ceiling with no data surgery. Storing a computed value would have
required editing history — which is exactly the "never manually patch state" trap.

**Prove a convenient zero.** After the fix I probed the container and got "spend:
0.00c". That looked like success and was garbage: I had run the probe with
`sh -c 'cd /app && ...'`, and both the events path *and* the timezone file resolve
**relative to cwd**. The real service runs from `/app/workspace`. Re-run from the right
cwd: 7.43c, deepseek unblocked, plus a 30-day control scan returning 19.57c to prove
the scanner *can* return non-zero.

That accident is finding 3.2 of the review demonstrating itself: twelve inline
`os.environ.get("HOMUNCULUS_*_DIR", "./relative")` defaults mean a `cd` silently
changes what the program reads. It is the same root cause as the stray repo-root
`proposals.json` and the stray `_events.jsonl`. **Any measurement that returns a
convenient zero is a hypothesis, not a result, until you show the same code path
returning non-zero.**

### Still open

Five of those seven expensive calls were `load_tool` round-trips loading **one tool
each** — full ~11.5K-token prompt per call to enable a schema. That is a cost
multiplier independent of the pricing bug, and it belongs to Level 2.
