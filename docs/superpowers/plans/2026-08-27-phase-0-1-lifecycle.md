# Phase 0 + Phase 1 — Turn Lifecycle Safety Net and Extraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin the turn lifecycle's observable behaviour with characterisation tests, then move it out of `core.py` into a `homunculus/agent/` package of focused modules with an explicit `TurnContext`, preserving behaviour exactly.

**Architecture:** Phase 0 adds a recorder that captures the ordered trace of one turn — every LLM call, tool dispatch, journal append and emitted event — and asserts against it. Phase 1 then converts `Agent`'s private phase methods into module-level functions taking `(agent, ctx)`, with `core.py` reduced to a façade. The recorder makes each move verifiable; nothing moves before it exists.

**Tech Stack:** Python 3.12, pytest 9, `uv`, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-08-27-remediation-design.md`

## Global Constraints

- **No capability is removed.** Every tool, skill and transport keeps working. This is a structural move, not a feature edit.
- **Behaviour is preserved exactly**, including behaviour later judged wrong. Anything Phase 0 pins that Phase 1 changes is a bug in Phase 1.
- **One concern per PR.** Each task below is one branch, one PR, one review.
- **Branch naming:** `test/`, `refactor/`, `fix/` + topic. Never push to `main`.
- **No `Co-Authored-By` trailer** on commits.
- **Comments carry no change history.** Explain the code and its rationale timelessly; the reason for a change belongs in the commit message.
- **Verification command set**, run in full before every commit:
  `uv run python -m pytest -q` · `uv run ruff check homunculus tests scripts` · `uv run pyright homunculus`
- **Coverage gate is live:** `pyproject.toml` sets `--cov-fail-under=60`. A move that drops coverage fails the suite; that is intended.
- **Ruff config:** line length 100, `E501` ignored, import sorting off. Do not reorder imports wholesale.
- **`LEARN.md` updates land in the same PR** as the code they describe.
- The model chain is settled. No task here touches `MODEL_FALLBACK` or `_MODEL_PRICING_CENTS`.

---

## Why Task 1 comes first: a silent-failure hazard specific to this refactor

Verified against the suite: **43 test sites call `patch.object(core, "call_llm", ...)`** and 14 call `monkeypatch.setattr(core, "_validate_tool_args", ...)`.

They work today because `core.py:41` does `from homunculus.llm import call_llm`, binding a *second* name in `core`'s namespace, and `Agent._call_model` resolves that module-global at call time.

The moment `_call_model` moves to `homunculus/agent/plan.py`, it resolves `call_llm` from **`plan.py`'s** globals. `patch.object(core, "call_llm")` still succeeds — the façade still has the attribute — but it no longer intercepts anything. The stub is never consulted and the test either reaches the real network or asserts vacuously against a fallback string.

This is precisely the failure signature the whole programme exists to eliminate: **a mechanism that stops applying and says nothing.** It must be closed *before* any code moves, which is why Task 1 is test-side only and touches no production file.

---

## File Structure

**Phase 0 — new test infrastructure**

| File | Responsibility |
|---|---|
| `tests/loop_recorder.py` | `TurnRecorder`: installs stubs, records the ordered trace of a turn. Not collected (no `test_` prefix). |
| `tests/test_characterisation_chat.py` | Pins the interactive (`source="web"`) turn. |
| `tests/test_characterisation_heartbeat.py` | Pins the autonomous (`source="heartbeat"`) turn: forced tools, early exit, `no_action`. |
| `tests/test_characterisation_guards.py` | Pins the deviation paths: local command, clarify-before-act, tool_choice violation, max_turns. |

**Phase 1 — the extracted package**

| File | Responsibility | Source |
|---|---|---|
| `homunculus/agent/__init__.py` | `Agent`: construction and public API (`chat`, `chat_stream`, `reflect`, `reset`, `restore_session`) | `core.py:583-680, 765-882` |
| `homunculus/agent/turn.py` | `TurnContext` — the per-turn state, currently 8 locals in `_run_loop` | new |
| `homunculus/agent/history.py` | `trim_tool_result_for_history`, `evict_prior_tool_results`, journal append/replace, message-id rebuild | `core.py:209-256, 269-334, 699-763` |
| `homunculus/output_guard.py` | keeps `strip_citation_artifacts`, renamed public (Task 5) | in place |
| `homunculus/agent/validation.py` | `validate_tool_args`, `clarify_before_act` | `core.py:485-538, 557-571` |
| `homunculus/agent/prompt.py` | `SYSTEM_PROMPT`, `current_system_prompt`, agents.md + memory-block loaders | `core.py:347-368, 371-483, 884-1088` |
| `homunculus/agent/plan.py` | `prepare_turn`, `loop_personality`, `call_model`, `pre_iteration_injections`, `handle_tool_choice_violation` | `core.py:1090-1148, 1529-1853` |
| `homunculus/agent/dispatch.py` | `dispatch_tool_calls`, `log_tool_call` | `core.py:1150-1465, 2210-2212` |
| `homunculus/agent/settle.py` | `finalize_reply`, `output_guard`, `nudge_for_reply`, `self_correct`, `maybe_compact`, `summarize_messages` | `core.py:1467-1527, 2128-2207, 2286-2375` |
| `homunculus/agent/commands.py` | `handle_local_command`, `format_tasks`, `local_status` | `core.py:2214-2282` |
| `homunculus/agent/loop.py` | `run_loop` — the orchestrator, readable top to bottom | `core.py:1855-2126` |
| `homunculus/core.py` | Façade: re-exports `Agent` and `SYSTEM_PROMPT` only | reduced |

**Deviation from the spec, declared:** the spec named six modules. This plan uses ten. `prompt.py`, `history.py`, `validation.py` and `commands.py` are split out because folding them into `plan.py` and `dispatch.py` would leave two ~600-line files — reproducing at half scale the problem the phase exists to fix. `history.py` and `validation.py` are already named in the spec's coupling-defects section; `prompt.py` and `commands.py` are the new pair. No file in the table above exceeds 400 lines.

**Interface convention:** phase functions are module-level and take `(agent, ctx, ...)` explicitly rather than becoming mixin methods on `Agent`. Mixins would preserve `self.x` with zero call-site churn, but they hide the data flow behind an MRO — which is the thing being fixed. Explicit parameters force `TurnContext` to be real and make each phase testable without constructing an `Agent`.

---

## Phase 0 — the safety net

### Task 1: Repoint LLM patch sites at the owning module

No production code changes. This closes the silent-failure hazard above before anything moves.

**Files:**
- Create: `tests/test_patch_site_hygiene.py`
- Modify: the 43 `patch.object(core, "call_llm")` sites and 14 `monkeypatch.setattr(core, "_validate_tool_args")` sites (enumerate with the grep in Step 1)

**Interfaces:**
- Consumes: nothing.
- Produces: the invariant *every LLM stub binds to `homunculus.llm.call_llm`, never to `homunculus.core.call_llm`* — relied on by every task after this one.

- [ ] **Step 1: Enumerate the sites**

```bash
cd /Users/umang/Projects/deep-learning-from-scratch/homunculus
grep -rn 'patch.object(core, "call_llm"\|monkeypatch.setattr(core, "call_llm"\|patch.object(core, "call_llm_stream"' tests/ | tee /tmp/llm_patch_sites.txt | wc -l
grep -rn 'setattr(core, "_validate_tool_args"\|patch.object(core, "_validate_tool_args"' tests/ | tee /tmp/val_patch_sites.txt | wc -l
```

Expected: 43 and 14. If the counts differ, the codebase moved — re-derive before continuing rather than assuming.

**Baseline, measured 2026-08-27 on `main`: 1,436 tests collected.** Every task
below quotes its own count against this number. A task that ends with fewer
tests than it started with has silently disabled something.

- [ ] **Step 2: Write the failing guard test**

Create `tests/test_patch_site_hygiene.py`:

```python
"""An LLM stub must bind to the module that owns the function.

`core.py` does `from homunculus.llm import call_llm`, which binds a second
name in `core`'s namespace. Patching that name works only while the caller
also lives in `core`. Move the caller to another module and the patch still
applies cleanly to an attribute nobody reads — the stub is silently bypassed
and the test asserts against whatever the real path returned.

Binding at `homunculus.llm` instead patches the single definition, so it
holds wherever the caller lives.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).parent

# `patch.object(core, "call_llm")` / `monkeypatch.setattr(core, "call_llm", ...)`
_REBOUND = re.compile(
    r'(?:patch\.object|monkeypatch\.setattr|setattr)\(\s*core\s*,\s*'
    r'"(call_llm|call_llm_stream|_validate_tool_args|validate_tool_args)"'
)


def test_no_test_patches_a_rebound_name_on_core():
    offenders: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _REBOUND.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These stubs bind a re-exported name on `core`, so they stop "
        "intercepting the moment the caller moves module.\n"
        "Patch the owner instead — `homunculus.llm.call_llm`, "
        "`homunculus.agent.validation.validate_tool_args`:\n  "
        + "\n  ".join(offenders)
    )
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `uv run python -m pytest tests/test_patch_site_hygiene.py -q --no-cov`
Expected: FAIL, listing 57 offenders.

- [ ] **Step 4: Repoint the LLM sites**

For each site in `/tmp/llm_patch_sites.txt`, change the target from the `core` attribute to the dotted path of the owner. Two forms appear in the suite; convert both.

```python
# before
with patch.object(core, "call_llm", side_effect=fake):
    out = "".join(agent._run_loop("tick", streaming=False, source="heartbeat"))

# after
with patch("homunculus.llm.call_llm", side_effect=fake):
    out = "".join(agent._run_loop("tick", streaming=False, source="heartbeat"))
```

```python
# before
monkeypatch.setattr(core, "call_llm", fake)

# after
monkeypatch.setattr("homunculus.llm.call_llm", fake)
```

Leave `_validate_tool_args` sites alone for now — Task 5 moves that function, and repointing them at `homunculus.core._validate_tool_args` would only have to change again. Add its name to the regex in Task 5, not here.

Because `core.py` still imports the name at module load, patching `homunculus.llm.call_llm` does **not** by itself rebind `core.call_llm`. Add to `core.py` (this is the one production edit in this task, and it is reverted in Task 10 when the importer disappears):

```python
def call_llm(*args, **kwargs):
    """Delegate to the owner so a stub installed on `homunculus.llm` is seen
    here too. The direct `from … import call_llm` binding froze the function
    at import time, which let a patch on the owner pass silently unapplied."""
    return llm.call_llm(*args, **kwargs)
```

with `from homunculus import llm` added to the import block and `call_llm` removed from the `from homunculus.llm import (...)` list at `core.py:39-44`. Apply the identical treatment to `call_llm_stream`.

- [ ] **Step 5: Narrow the guard to what this task fixed**

Drop `_validate_tool_args` and `validate_tool_args` from the `_REBOUND` alternation so the guard passes on the state this task leaves behind. Task 5 adds them back.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: PASS, same count as before this task (record the number in the PR body — a *drop* means a stub is now intercepting something it previously missed, which is a real finding to report, not to paper over).

- [ ] **Step 7: Lint and typecheck**

Run: `uv run ruff check homunculus tests scripts && uv run pyright homunculus`
Expected: clean.

- [ ] **Step 8: Commit and open the PR**

```bash
git checkout -b test/patch-llm-at-owner
git add tests/ homunculus/core.py
git commit -m "test: bind LLM stubs to the owning module, not core's re-export

A stub on core.call_llm intercepts only while the caller lives in core.
Binding at homunculus.llm patches the definition, so it survives the
lifecycle extraction. A guard test fails any new site that rebinds."
git push -u origin test/patch-llm-at-owner
gh pr create --fill
```

---

### Task 2: The turn recorder

**Files:**
- Create: `tests/loop_recorder.py`
- Modify: `tests/conftest.py` (add the `record_turn` fixture)

**Interfaces:**
- Consumes: the Task 1 invariant (stubs bind at `homunculus.llm`).
- Produces:
  - `TurnRecorder.run(user_message: str, *, source: str = "web", replies: list[dict], tool_results: dict[str, str] | None = None, **loop_kwargs) -> list[str]`
  - `TurnRecorder.trace() -> list[str]` — the ordered, human-readable turn trace
  - `TurnRecorder.llm_calls: list[dict]` — one entry per model call: `{"messages": [...], "tool_choice": ..., "reasoning_effort": ...}`
  - pytest fixture `record_turn` yielding a `TurnRecorder`

- [ ] **Step 1: Write the recorder**

Create `tests/loop_recorder.py`:

```python
"""Record the ordered, observable trace of one agent turn.

The suite has 1,400-odd tests, and they pin behaviours one at a time: this
guard fires, that early exit works. None of them pin the *sequence* — which
is the only thing a structural refactor can break without breaking any
single assertion.

A trace here is a flat list of short strings, one per observable event, in
the order the turn produced them. Asserting on the whole list means a phase
that moves, disappears, or fires twice shows up as a diff instead of as a
still-green suite.

The trace deliberately records what a caller could observe — model calls,
tool dispatches, journal writes, emitted events, the yielded reply. It does
not record internal locals, so a refactor is free to change them.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from homunculus import core, events
import homunculus.tools as tools_module


def tool_call(name: str, cid: str = "c1", args: dict | None = None) -> dict:
    """One entry for an assistant message's `tool_calls` array."""
    return {
        "id": cid,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}),
        },
    }


def says(text: str) -> dict:
    """A model reply with no tool calls — the turn's terminal message."""
    return {"role": "assistant", "content": text}


def calls(*names: str) -> dict:
    """A model reply that calls tools, one call per name."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [tool_call(n, cid=f"c{i}") for i, n in enumerate(names)],
    }


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "recorder stub",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class TurnRecorder:
    """Drives one `_run_loop` against scripted model replies and records it."""

    def __init__(self, monkeypatch, tool_names: tuple[str, ...]):
        self._monkeypatch = monkeypatch
        self._events: list[str] = []
        self._trace: list[str] = []
        self.llm_calls: list[dict] = []
        self.dispatched: list[tuple[str, dict]] = []
        self._tool_results: dict[str, str] = {}
        monkeypatch.setattr(
            tools_module, "SCHEMAS", [_schema(n) for n in tool_names], raising=False
        )
        monkeypatch.setattr(tools_module, "execute", self._execute, raising=False)
        monkeypatch.setattr(events, "emit", self._emit, raising=False)

    # -- stubs -------------------------------------------------------

    def _execute(self, name: str, args: dict) -> str:
        self.dispatched.append((name, args))
        self._trace.append(f"tool:{name}")
        return self._tool_results.get(name, "OK")

    def _emit(self, event: str, **fields: Any) -> None:
        self._events.append(event)
        self._trace.append(f"event:{event}")

    # -- driving -----------------------------------------------------

    def run(
        self,
        user_message: str,
        *,
        source: str = "web",
        replies: list[dict],
        tool_results: dict[str, str] | None = None,
        agent: Any = None,
        **loop_kwargs: Any,
    ) -> list[str]:
        """Run one turn. `replies` is consumed one entry per model call;
        running past the end raises rather than looping on a stale reply."""
        self._tool_results = tool_results or {}
        agent = agent if agent is not None else core.Agent(memory=None)
        pending = list(replies)

        def fake_call_llm(messages, tool_schemas, model=None, tool_choice="auto",
                          reasoning_effort="low", provider_constraints=None):
            self.llm_calls.append({
                "messages": [dict(m) for m in messages],
                "tool_choice": tool_choice,
                "reasoning_effort": reasoning_effort,
            })
            self._trace.append(f"llm:{_choice_label(tool_choice)}")
            if not pending:
                raise AssertionError(
                    f"the loop made {len(self.llm_calls)} model calls but the "
                    f"script supplied {len(replies)}. Either the turn does not "
                    "exit where the test assumes, or the script is short."
                )
            return pending.pop(0)

        with patch("homunculus.llm.call_llm", side_effect=fake_call_llm):
            out = list(agent._run_loop(
                user_message, streaming=False, source=source, **loop_kwargs
            ))
        for chunk in out:
            self._trace.append(f"yield:{chunk}")
        self.agent = agent
        return out

    # -- assertions --------------------------------------------------

    def trace(self) -> list[str]:
        return list(self._trace)

    def events(self) -> list[str]:
        return list(self._events)


def _choice_label(tool_choice: Any) -> str:
    """Collapse tool_choice to one token: `auto`, `required`, or `forced:name`."""
    if isinstance(tool_choice, dict):
        return f"forced:{tool_choice.get('function', {}).get('name', '?')}"
    return str(tool_choice)
```

- [ ] **Step 2: Add the fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def record_turn(monkeypatch):
    """A `TurnRecorder` over a small stub tool set.

    Widen the set per-test by passing `tool_names` to `TurnRecorder` directly;
    the default covers the tools the lifecycle itself reaches for.
    """
    from tests.loop_recorder import TurnRecorder

    return TurnRecorder(monkeypatch, tool_names=(
        "notify", "list_tasks", "complete_task", "continue_task",
        "cancel_task", "record_failure", "no_action", "recall",
        "get_current_time", "read_file",
    ))
```

`tests/conftest.py` must already `import pytest`; add it to the import block if it does not.

- [ ] **Step 3: Prove the recorder records**

Add to `tests/loop_recorder.py`'s companion — create `tests/test_characterisation_chat.py` with one smoke test for now:

```python
"""The turn recorder itself must be trustworthy before anything relies on it."""

from __future__ import annotations

from tests.loop_recorder import calls, says


def test_recorder_captures_a_two_call_turn(record_turn):
    out = record_turn.run(
        "check my tasks",
        replies=[calls("list_tasks"), says("You have two open tasks.")],
    )
    assert out == ["You have two open tasks."]
    assert record_turn.dispatched == [("list_tasks", {})]
    assert len(record_turn.llm_calls) == 2
    assert "tool:list_tasks" in record_turn.trace()
```

- [ ] **Step 4: Run it**

Run: `uv run python -m pytest tests/test_characterisation_chat.py -q --no-cov`
Expected: PASS. If `tests.loop_recorder` fails to import, the suite lacks `tests/__init__.py` — it exists (verified), so an import error means the path, not the packaging.

- [ ] **Step 5: Full suite, lint, typecheck**

Run: `uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus`
Expected: clean. The `events.emit` monkeypatch is fixture-scoped, so no other test sees it.

- [ ] **Step 6: Commit**

```bash
git checkout -b test/turn-recorder
git add tests/loop_recorder.py tests/conftest.py tests/test_characterisation_chat.py
git commit -m "test: record the ordered trace of an agent turn

The suite pins behaviours individually and the sequence not at all, which
is the part a structural move can break silently. The recorder captures
model calls, dispatches and events in order so a diff is visible."
git push -u origin test/turn-recorder && gh pr create --fill
```

---

### Task 3: Characterise the interactive turn

**Files:**
- Modify: `tests/test_characterisation_chat.py`

**Interfaces:**
- Consumes: `TurnRecorder`, `calls`, `says` from Task 2.
- Produces: the pinned `source="web"` trace, relied on by Tasks 6-11 as the pass/fail signal for the extraction.

- [ ] **Step 1: Pin the whole sequence of a tool-using chat turn**

Append to `tests/test_characterisation_chat.py`:

```python
def test_chat_turn_sequence_is_pinned(record_turn):
    """The full observable order of a one-tool chat turn.

    This asserts the list, not membership. A phase that moves earlier, runs
    twice, or stops running changes the list even when every other test in
    the suite still passes — which is the whole point.
    """
    record_turn.run(
        "what time is it",
        replies=[calls("get_current_time"), says("It is 14:05.")],
    )
    assert record_turn.trace() == [
        "event:user_message",
        "llm:auto",
        "tool:get_current_time",
        "llm:auto",
        "event:assistant_reply",
        "yield:It is 14:05.",
    ]
```

- [ ] **Step 2: Run it and record what it actually does**

Run: `uv run python -m pytest tests/test_characterisation_chat.py::test_chat_turn_sequence_is_pinned -q --no-cov`

Expected: **FAIL on first run.** The list above is the sequence the code is *believed* to produce; the real one includes events this plan cannot predict from a static read (`world_state` writes, guard passes, permission checks). Paste the actual list from the assertion diff into the test verbatim, then re-run to PASS.

This is the characterisation-testing discipline: the test records what the code does, not what it should do. Do **not** "fix" a surprising entry — note it in the PR body as an observation. If an entry looks like a bug, it belongs in the spec's findings list and a later phase, never in this PR.

- [ ] **Step 3: Pin the plain-reply turn**

```python
def test_a_reply_with_no_tool_calls_takes_one_model_call(record_turn):
    out = record_turn.run("hello", replies=[says("Hi Umang.")])
    assert out == ["Hi Umang."]
    assert len(record_turn.llm_calls) == 1
```

- [ ] **Step 4: Pin the system prompt's position and freshness**

```python
def test_the_system_prompt_is_message_zero_and_rebuilt_each_turn(record_turn):
    """`_prepare_turn` overwrites history[0] every turn so the clock is
    current. Extraction must not turn that into a build-once."""
    record_turn.run("first", replies=[says("ok")])
    record_turn.run("second", replies=[says("ok")], agent=record_turn.agent)

    for call in record_turn.llm_calls:
        assert call["messages"][0]["role"] == "system"
    first, second = record_turn.llm_calls
    assert first["messages"][0]["content"] is not None
    # The user turn is appended, so the second call carries strictly more.
    assert len(second["messages"]) > len(first["messages"])
```

- [ ] **Step 5: Pin history growth**

```python
def test_history_after_a_tool_turn(record_turn):
    """system, user, assistant(tool_calls), tool, assistant(final)."""
    record_turn.run(
        "check tasks", replies=[calls("list_tasks"), says("Two open.")],
    )
    roles = [m["role"] for m in record_turn.agent.history]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
```

- [ ] **Step 6: Run, lint, typecheck, commit**

```bash
uv run python -m pytest tests/test_characterisation_chat.py -q --no-cov
uv run python -m pytest -q && uv run ruff check homunculus tests scripts
git checkout -b test/characterise-chat-turn
git add tests/test_characterisation_chat.py
git commit -m "test: pin the observable sequence of an interactive turn"
git push -u origin test/characterise-chat-turn && gh pr create --fill
```

---

### Task 4: Characterise the autonomous turn and the deviation paths

**Files:**
- Create: `tests/test_characterisation_heartbeat.py`
- Create: `tests/test_characterisation_guards.py`

**Interfaces:**
- Consumes: `TurnRecorder`, `calls`, `says`.
- Produces: pinned traces for `source="heartbeat"`, `no_action`, `expected_completions`, local commands, clarify-before-act, tool_choice violations and `max_turns`.

Every test below follows the Task 3 discipline: write the expected list, run it, paste the real one, re-run green, note surprises in the PR body.

- [ ] **Step 1: Pin the heartbeat personality**

Create `tests/test_characterisation_heartbeat.py`:

```python
"""The autonomous turn differs from the interactive one in three ways:
tool_choice, the early exits, and the reply strings. All three are pinned
here because all three live in code Phase 1 moves."""

from __future__ import annotations

from tests.loop_recorder import calls, says


def test_heartbeat_requires_a_tool_call_every_turn(record_turn):
    record_turn.run(
        "tick", source="heartbeat",
        replies=[calls("list_tasks"), says("Nothing due.")],
    )
    assert [c["tool_choice"] for c in record_turn.llm_calls] == ["required", "required"]


def test_web_does_not_require_a_tool_call(record_turn):
    record_turn.run("hello", source="web", replies=[says("Hi.")])
    assert record_turn.llm_calls[0]["tool_choice"] == "auto"
```

- [ ] **Step 2: Pin `no_action` as a turn terminator**

```python
def test_no_action_ends_the_turn_immediately(record_turn):
    """`no_action` means "I checked, nothing to do". Under tool_choice=required
    a turn that continues can only ask the same question again."""
    out = record_turn.run(
        "tick", source="heartbeat", replies=[calls("no_action")],
    )
    assert out == ["✓ Nothing to do."]
    assert len(record_turn.llm_calls) == 1
    assert "event:loop_exit_on_completion" in record_turn.trace()
```

- [ ] **Step 3: Pin the expected-completions early exit**

```python
def test_closing_the_declared_task_count_exits(record_turn):
    out = record_turn.run(
        "tick", source="heartbeat",
        replies=[calls("complete_task")],
        expected_completions=1,
    )
    assert out == ["✓ Done."]
    assert len(record_turn.llm_calls) == 1


def test_a_second_declared_task_keeps_the_loop_open(record_turn):
    out = record_turn.run(
        "tick", source="heartbeat",
        replies=[calls("complete_task"), calls("complete_task")],
        expected_completions=2,
    )
    assert out == ["✓ Done."]
    assert len(record_turn.llm_calls) == 2
```

- [ ] **Step 4: Pin the pre-loop deviation paths**

Create `tests/test_characterisation_guards.py`:

```python
"""Four ways a turn ends without reaching the model, or without reaching a
reply. Each is a distinct early return in `_run_loop`; the extraction must
keep all four, in this order of precedence."""

from __future__ import annotations

from tests.loop_recorder import calls, says


def test_a_local_command_never_reaches_the_model(record_turn):
    out = record_turn.run("/status", replies=[])
    assert len(record_turn.llm_calls) == 0
    assert out and isinstance(out[0], str)


def test_an_ambiguous_imperative_asks_instead_of_acting(record_turn):
    """"Set it up" with no referent in history is a clarify, not a task."""
    out = record_turn.run("set it up", replies=[])
    assert len(record_turn.llm_calls) == 0
    assert "event:clarify_before_act" in record_turn.trace()
    assert out and "?" in out[0]
```

Run these two first — the clarify trigger depends on `_AMBIGUOUS_IMPERATIVE_RE` at `core.py:548`. Read that regex and pick a phrase it genuinely matches rather than trusting the example.

- [ ] **Step 5: Pin the tool_choice violation retry**

```python
def test_prose_when_a_tool_was_required_triggers_one_retry(record_turn):
    """A provider that ignores tool_choice gets a synthetic correction and
    another turn, capped at 2 per run."""
    out = record_turn.run(
        "tick", source="heartbeat",
        replies=[says("I have completed the task."), calls("complete_task"),
                 says("Done.")],
    )
    assert len(record_turn.llm_calls) >= 2
    assert out
```

- [ ] **Step 6: Pin the max_turns fallback**

```python
def test_a_model_that_never_finishes_hits_the_ceiling(record_turn, monkeypatch):
    from homunculus.config import get_config
    monkeypatch.setattr(get_config().loop, "max_turns", 3, raising=False)
    out = record_turn.run(
        "tick", source="heartbeat",
        replies=[calls("list_tasks")] * 3,
    )
    assert out == ["(hit max_turns without a final answer)"]
    assert len(record_turn.llm_calls) == 3
```

If `get_config()` returns a frozen model, set the ceiling through whatever `set_config` helper the suite already uses — `tests/test_max_turns_nudge.py` shows the house method; copy it rather than inventing one.

- [ ] **Step 7: Run, lint, commit**

```bash
uv run python -m pytest tests/test_characterisation_heartbeat.py tests/test_characterisation_guards.py -q --no-cov
uv run python -m pytest -q && uv run ruff check homunculus tests scripts
git checkout -b test/characterise-autonomous-turn
git add tests/test_characterisation_heartbeat.py tests/test_characterisation_guards.py
git commit -m "test: pin the autonomous turn and the four early-exit paths"
git push -u origin test/characterise-autonomous-turn && gh pr create --fill
```

- [ ] **Step 8: Write the Phase 0 exit note**

Add to the PR body, and to `docs/superpowers/specs/2026-08-27-remediation-design.md` under Phase 0 as a "landed" line: the total test count before and after, and every surprise found in Steps 2/4/5 of Tasks 3-4. Phase 1 does not start until this note exists — it is the record of what "unchanged" means.

---

## Phase 1 — make the lifecycle readable

Each task from here is a pure move: the diff should be dominated by deletions from `core.py` and identical additions elsewhere. **If a task's diff contains a logic change, split it out.** The characterisation suite is the arbiter — it must stay green at every step, with no test edited except for import paths.

### Task 5: Extract `agent/validation.py`

Smallest real move first: two pure functions, 69 lines, no `self`.

**Files:**
- Create: `homunculus/agent/__init__.py` (empty placeholder for now), `homunculus/agent/validation.py`
- Modify: `homunculus/core.py:485-538, 557-571` (delete, re-export), `tests/test_patch_site_hygiene.py`, importers of `_validate_tool_args` / `_clarify_before_act`

**Interfaces:**
- Produces:
  - `validate_tool_args(name: str, arguments: dict) -> str | None` — returns an error string, or `None` when the args are acceptable
  - `clarify_before_act(user_message: str, history: list[dict]) -> str | None` — returns a clarifying question, or `None` to proceed
  - module constant `AMBIGUOUS_IMPERATIVE_RE: re.Pattern[str]`
  - (in `homunculus/output_guard.py`, not this module) `strip_citation_artifacts(content: str) -> str`

- [ ] **Step 1: Find every importer**

```bash
grep -rn '_validate_tool_args\|_clarify_before_act\|_AMBIGUOUS_IMPERATIVE_RE\|_strip_citation_artifacts' homunculus/ tests/ scripts/
```

Record the list. Every one is updated in this task; none may be left pointing at `core`.

- [ ] **Step 2: Create the package and move the code**

```bash
mkdir -p homunculus/agent
touch homunculus/agent/__init__.py
```

Move `core.py:485-538` and `core.py:548-571` verbatim into `homunculus/agent/validation.py`, dropping the leading underscore from all three names. Carry their imports (`re`, and whatever the bodies reference — read them, do not guess). Give the module a docstring stating what it validates and why these two live together: both answer *"should this call happen at all?"* before anything is dispatched.

- [ ] **Step 3: Re-export from core, do not re-implement**

In `core.py`, replace the deleted definitions with:

```python
from homunculus.agent.validation import (
    validate_tool_args as _validate_tool_args,
    clarify_before_act as _clarify_before_act,
)
```

This keeps `core._validate_tool_args` resolving for the 14 stub sites *for the duration of this step only*.

- [ ] **Step 4: Run the suite against the alias**

Run: `uv run python -m pytest -q`
Expected: PASS, unchanged count. This proves the move is behaviour-neutral before the call sites churn.

- [ ] **Step 5: Repoint the 14 stub sites**

```python
# before
monkeypatch.setattr(core, "_validate_tool_args", fake)

# after
monkeypatch.setattr("homunculus.agent.validation.validate_tool_args", fake)
```

Note the same hazard as Task 1: `dispatch` will call `validate_tool_args` from its own module namespace once Task 8 moves it, so patch the owner.

- [ ] **Step 6: Delete the aliases and repoint production importers**

Remove the `as _validate_tool_args` aliasing from `core.py` and update every importer found in Step 1 to `from homunculus.agent.validation import validate_tool_args`.

- [ ] **Step 7: De-underscore `strip_citation_artifacts` and repoint its importers**

Third cross-boundary private, and the clearest illustration of the hub: it is
defined at `homunculus/output_guard.py:34`, and `tests/test_output_guard.py:332`
imports it **from `core`** rather than from the module two lines above its own
subject. Rename it to `strip_citation_artifacts` in `output_guard.py`, update
its one internal caller at `output_guard.py:419`, and repoint both importers:

```python
# tests/test_output_guard.py
from homunculus.output_guard import strip_citation_artifacts

# homunculus/core.py:70 — drop from the import block entirely; the one
# call site at core.py:2031 moves to loop.py in Task 10 and imports the
# owner directly.
```

It does not move into `homunculus/agent/`. Citation-marker stripping is
output-guard business that the loop happens to call; relocating it would trade
a wrong name for a wrong home.

- [ ] **Step 8: Re-arm the guard**

Add `_validate_tool_args` and `validate_tool_args` back to the `_REBOUND` alternation in `tests/test_patch_site_hygiene.py`.

- [ ] **Step 9: Verify and commit**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
git checkout -b refactor/agent-validation-module
git add homunculus/agent tests/ homunculus/core.py
git commit -m "refactor: move pre-dispatch validation into homunculus.agent.validation

A leading underscore promises internal, and both functions were imported
across module boundaries. They are public in the module that owns them."
git push -u origin refactor/agent-validation-module && gh pr create --fill
```

---

### Task 6: Extract `agent/history.py`

**Files:**
- Create: `homunculus/agent/history.py`
- Modify: `homunculus/core.py` (delete `209-256`, `269-334`, `699-763`)

**Interfaces:**
- Produces:
  - `trim_tool_result_for_history(name: str, result: object) -> object`
  - `evict_prior_tool_results(history: list[dict]) -> int` — returns the number of results stubbed
  - `journal_append(agent, msg: dict) -> None`
  - `journal_replace_last_content(agent, new_content: str) -> None`
  - `rebuild_message_ids_after_compaction(agent, summary_msg: dict, kept_tail: list[dict], cut_at: int) -> None`
  - module constants `TAIL_PRESERVING_TOOLS`, `EVICTED_TOOL_RESULT_TEMPLATE`

The three `journal_*` functions take `agent` because they touch `agent.history`, `agent._transcript` and `agent._message_ids`. They are the first functions to take the object rather than be a method on it; the docstring should say that history and its transcript mirror are one concern, and that keeping them together is what stops the two drifting.

- [ ] **Step 1: Move the two pure functions and their constants**

Move `core.py:160` (`_TAIL_PRESERVING_TOOLS`), `209-256`, `262-267` (`_EVICTED_TOOL_RESULT_TEMPLATE`), `269-334` verbatim. Drop the underscores.

- [ ] **Step 2: Convert the three journal methods to functions**

```python
def journal_append(agent, msg: dict) -> None:
    """Append `msg` to history and mirror it to the transcript."""
    agent.history.append(msg)
    if agent._transcript is not None:
        try:
            rid = agent._transcript.append(msg)
            agent._message_ids.append(rid)
        except Exception as e:
            events.emit(
                "transcript_drift",
                text=f"journal_append failed: {type(e).__name__}: {e}",
            )
```

Copy the remaining two the same way, substituting `agent` for `self`. Change nothing else — not the exception breadth, not the event names.

- [ ] **Step 3: Keep `Agent` working via delegation**

In `core.py`, the `Agent` methods become one-liners so no call site changes in this task:

```python
    def _journal_append(self, msg: dict) -> None:
        history.journal_append(self, msg)
```

- [ ] **Step 4: Repoint importers of the pure functions**

`tests/test_evict_prior_tool_results.py`, `tests/test_evict_integration.py` and anything else Step 1 of Task 5's grep pattern finds for these two names.

- [ ] **Step 5: Verify and commit**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
git checkout -b refactor/agent-history-module
git add homunculus/ tests/
git commit -m "refactor: move history and transcript journaling into homunculus.agent.history

History and its transcript mirror are one concern; splitting them across
files is how the two drift."
git push -u origin refactor/agent-history-module && gh pr create --fill
```

---

### Task 7: Extract `agent/prompt.py`

The largest single move (≈340 lines) and the lowest risk — the system prompt is a string and three loaders.

**Files:**
- Create: `homunculus/agent/prompt.py`
- Modify: `homunculus/core.py` (delete `347-368`, `371-483`, `884-1088`)

**Interfaces:**
- Produces:
  - `SYSTEM_PROMPT: str`
  - `current_system_prompt(agent, source: str = "") -> str`
  - `load_agents_md_cached(agent) -> str`
  - `load_memory_block_cached(agent) -> str`
  - `resolve_agents_md() -> Path | None`

- [ ] **Step 1: Move verbatim, converting the three methods to `agent`-taking functions**

Same mechanical substitution as Task 6. `SYSTEM_PROMPT` moves as a plain string literal; do not reflow it, and do not touch the cache-prefix ordering — the layering is deliberate and load-bearing for the provider's prefix cache.

- [ ] **Step 2: `core.py` keeps `SYSTEM_PROMPT` as a re-export**

```python
from homunculus.agent.prompt import SYSTEM_PROMPT as SYSTEM_PROMPT
```

Seven modules and tests import it from `core`; they are repointed in Task 11, not here.

- [ ] **Step 3: Delegate the methods**

```python
    def _current_system_prompt(self, source: str = "") -> str:
        return prompt.current_system_prompt(self, source)
```

- [ ] **Step 4: Verify the prompt is byte-identical**

```bash
uv run python -c "
from homunculus.agent.prompt import SYSTEM_PROMPT as new
import hashlib; print(hashlib.sha256(new.encode()).hexdigest())"
```

Record the hash in the PR body next to the same hash computed on `main` before the move. They must match. A one-character drift in the system prompt is a behaviour change that no test would catch.

- [ ] **Step 5: Verify and commit**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
git checkout -b refactor/agent-prompt-module
git add homunculus/
git commit -m "refactor: move the system prompt and its loaders into homunculus.agent.prompt"
git push -u origin refactor/agent-prompt-module && gh pr create --fill
```

---

### Task 8: Introduce `TurnContext`

The load-bearing task. Nothing moves; the eight per-turn locals in `_run_loop` become one object.

**Files:**
- Create: `homunculus/agent/turn.py`
- Modify: `homunculus/core.py:1855-2126` (`_run_loop`), `1150-1465` (`_dispatch_tool_calls`), `1467-1527` (`_finalize_reply`), `1629-1746`, `1748-1853`

**Interfaces:**
- Produces:

```python
@dataclass
class TurnContext:
    """The state one turn accumulates, and nothing else.

    These eight fields are currently locals in the loop body, threaded into
    the phases as up to six positional arguments. Naming them makes the data
    flow visible and lets a phase be tested without running a loop.

    Construction-time configuration — model, permissions, memory, transcript
    — stays on `Agent`: it is not per-turn and putting it here would make the
    context a second copy of the agent.
    """
    user_message: str
    source: str
    tool_names_used: set[str] = field(default_factory=set)
    call_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    per_tool_counts: dict[str, int] = field(default_factory=dict)
    tool_result_cache: dict[tuple[str, str], str] = field(default_factory=dict)
    tool_outcomes: list[dict] = field(default_factory=list)
    terminal_completions: int = 0
    required_tool_violations: int = 0
    state_idx: int = 0
```

- [ ] **Step 1: Write the dataclass**

Create `homunculus/agent/turn.py` with the above, plus `from __future__ import annotations`, `from dataclasses import dataclass, field`.

- [ ] **Step 2: Construct it in `_run_loop`**

Replace the eight local initialisations (`core.py:1926-1934`, `1975`, `1994`, `2000`) with:

```python
        ctx = TurnContext(user_message=user_message, source=source)
```

- [ ] **Step 3: Change the phase signatures one at a time, running the suite between each**

```python
    def _dispatch_tool_calls(self, tool_calls: list[dict], ctx: TurnContext) -> int:
    def _finalize_reply(self, assistant_msg: dict, ctx: TurnContext) -> str | None:
    def _pre_iteration_injections(self, turn_idx: int, max_turns: int, ctx: TurnContext) -> None:
    def _handle_tool_choice_violation(
        self, tool_calls, turn_tool_choice, assistant_msg,
        state_sequence, ctx: TurnContext,
    ) -> bool:
```

Note `_handle_tool_choice_violation` now returns a bare `bool` — it mutated `state_idx` and `required_tool_violations` through its return tuple only because it had no shared object to write to. It writes them on `ctx` instead. The same applies to `_dispatch_tool_calls`: it can add to `ctx.terminal_completions` directly rather than returning a count. **Keep the `int` return for this task** and change it in Task 9; one signature change per step is what keeps the failure attributable.

- [ ] **Step 4: Update the focused tests that call these methods directly**

```bash
grep -rn '_dispatch_tool_calls(\|_finalize_reply(\|_pre_iteration_injections(\|_handle_tool_choice_violation(' tests/
```

Each becomes a `TurnContext(...)` construction instead of five bare collections. This is the readability win landing in the tests as well as the code — say so in the PR body.

- [ ] **Step 5: Verify and commit**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
git checkout -b refactor/turn-context
git add homunculus/ tests/
git commit -m "refactor: give the turn's state a name

Eight loop locals threaded through six-parameter calls become one
TurnContext. The phases now declare what a turn is made of."
git push -u origin refactor/turn-context && gh pr create --fill
```

---

### Task 9: Extract `agent/dispatch.py` and `agent/settle.py`

**Files:**
- Create: `homunculus/agent/dispatch.py`, `homunculus/agent/settle.py`
- Modify: `homunculus/core.py` (delete `1150-1465`, `2210-2212`, `1467-1527`, `2128-2207`, `2286-2375`)

**Interfaces:**
- Produces from `dispatch.py`:
  - `dispatch_tool_calls(agent, tool_calls: list[dict], ctx: TurnContext) -> None` — increments `ctx.terminal_completions` in place
  - `log_tool_call(name: str, args: dict[str, Any]) -> None`
- Produces from `settle.py`:
  - `finalize_reply(agent, assistant_msg: dict, ctx: TurnContext) -> str | None`
  - `run_output_guard(agent, reply: str, ctx: TurnContext) -> tuple[str | None, list[str]]`
  - `nudge_for_reply(agent) -> str`
  - `self_correct(agent, ctx: TurnContext, violations: list[str] | None = None) -> str`
  - `maybe_compact(agent) -> None`
  - `summarize_messages(agent, messages: list[dict]) -> str`
  - `flatten_message_for_summary(msg: dict) -> str`

`run_output_guard` is renamed from `_output_guard` because `output_guard` is already a module name in this package; two things called `output_guard` in one import graph is the ambiguity that makes a reader check.

- [ ] **Step 1: Move `dispatch`, converting `self` to `agent`**

Move `core.py:1150-1465` and `2210-2212` verbatim into `dispatch.py`. Change the terminal-completion return to an in-place `ctx.terminal_completions += 1` at the site that currently does `closed += 1`, and drop the `closed` local and the `return closed`.

- [ ] **Step 2: Update the one call site**

```python
            dispatch.dispatch_tool_calls(self, tool_calls, ctx)
```

replaces `terminal_completions += self._dispatch_tool_calls(...)` at `core.py:2073`.

- [ ] **Step 3: Run the suite**

Run: `uv run python -m pytest -q`
Expected: PASS. `tests/test_characterisation_heartbeat.py::test_closing_the_declared_task_count_exits` is the specific test that proves the counter still advances; if it fails, the increment moved but the read did not.

- [ ] **Step 4: Move `settle`**

Move `core.py:1467-1527`, `2128-2141`, `2148-2164`, `2166-2207`, `2286-2341`, `2343-2364`, `2367-2375` into `settle.py`. `maybe_compact` and `summarize_messages` belong here rather than in `history.py`: compaction is what a turn does when it *ends*, and it calls the model to do it. Say that in the module docstring.

- [ ] **Step 5: Split the 316-line dispatch body into named steps**

Still inside `dispatch.py`, and only after Steps 1-4 are green. `dispatch_tool_calls` becomes a loop over calls whose body reads as a sequence of named helpers, one per stage the existing docstring already lists:

```python
def dispatch_tool_calls(agent, tool_calls: list[dict], ctx: TurnContext) -> None:
    for call in tool_calls:
        name, args = _parse_call(call)
        decision = _apply_permissions(agent, name, args)
        if decision.blocked:
            _journal_refusal(agent, call, decision, ctx)
            continue
        if (cached := _cache_lookup(ctx, name, decision.args)) is not None:
            _journal_result(agent, call, name, cached, ctx)
            continue
        if (error := _schema_check(name, decision.args)) is not None:
            _journal_result(agent, call, name, error, ctx)
            continue
        result = _execute(agent, name, decision.args, ctx)
        _record_outcome(ctx, name, decision.args, result)
        _journal_result(agent, call, name, result, ctx)
```

The exact stage list must be derived by reading `core.py:1150-1465` — the sketch above is the shape, not a specification of the branches. **Every branch present today must remain present**, including the loop/stuck detector and the per-tool cap. If the real body does not decompose into single-purpose helpers without duplicating a branch, stop and leave it whole: a faithful 316-line function is better than a decomposition that quietly changes an edge case. Report that outcome rather than forcing it.

- [ ] **Step 6: Verify and commit as two PRs**

Steps 1-4 are one PR (`refactor/agent-dispatch-settle`); Step 5 is a second (`refactor/decompose-dispatch`). They are different concerns — one moves code, one restructures it — and the second is the only one in Phase 1 where a reviewer must read for behaviour rather than for a diff of moves.

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
```

---

### Task 10: Extract `agent/loop.py`, `agent/plan.py`, `agent/commands.py`, and the `Agent` class

**Files:**
- Create: `homunculus/agent/loop.py`, `homunculus/agent/plan.py`, `homunculus/agent/commands.py`
- Modify: `homunculus/agent/__init__.py` (gains `Agent`), `homunculus/core.py` (becomes the façade)

**Interfaces:**
- Produces:
  - `homunculus.agent.Agent` — same public API as today: `chat`, `chat_stream`, `reflect`, `reset`, `restore_session`, `history`, `memory`, `permissions`
  - `loop.run_loop(agent, user_message: str, streaming: bool, source: str = "web", state_sequence: list[dict] | None = None, expected_completions: int | None = None)` — generator
  - `plan.prepare_turn(agent, ctx: TurnContext) -> None`
  - `plan.loop_personality(source: str) -> tuple[str, str]`
  - `plan.call_model(agent, ctx, state_sequence, tool_choice, streaming, reasoning_effort, provider_constraints) -> tuple[dict | None, str]`
  - `plan.pre_iteration_injections(agent, turn_idx: int, max_turns: int, ctx: TurnContext) -> None`
  - `plan.handle_tool_choice_violation(agent, tool_calls, turn_tool_choice, assistant_msg, state_sequence, ctx) -> bool`
  - `commands.handle_local_command(agent, user_message: str) -> str | None`

`call_model` loses `state_idx` from its return tuple — it writes `ctx.state_idx` instead, as `handle_tool_choice_violation` already does after Task 8.

- [ ] **Step 1: Move `plan` and `commands`**

`core.py:1090-1132`, `1135-1148`, `1529-1627`, `1629-1746`, `1748-1853` → `plan.py`. `core.py:2214-2249`, `2251-2264`, `2266-2282` → `commands.py`.

- [ ] **Step 2: Move `_run_loop` into `loop.py` as `run_loop(agent, ...)`**

The body changes only in how it names its collaborators: `self._prepare_turn(...)` becomes `plan.prepare_turn(agent, ctx)`, `self._dispatch_tool_calls(...)` becomes `dispatch.dispatch_tool_calls(agent, tool_calls, ctx)`, and so on. **No control flow changes in this step.** The comment blocks explaining *why* each early exit exists move with the code they explain.

- [ ] **Step 3: Move `Agent` into `agent/__init__.py`**

`core.py:576-680` (class header and `__init__`), `765-783` (`reset`), `785-820` (`restore_session`), `822-832` (`reflect`), `834-863` (`chat`), `865-882` (`chat_stream`). The remaining private methods become thin delegations or disappear entirely where the loop now calls the module function directly.

`chat` and `chat_stream` keep their exact signatures — nine transports call them.

- [ ] **Step 4: Reduce `core.py` to a façade**

```python
"""Backwards-compatible entry point for the agent.

The turn lifecycle lives in `homunculus.agent`. This module exists because
several transports and tests import `Agent` and `SYSTEM_PROMPT` from here;
Task 11 repoints them, after which this file can go.
"""

from homunculus.agent import Agent as Agent
from homunculus.agent.prompt import SYSTEM_PROMPT as SYSTEM_PROMPT

__all__ = ["Agent", "SYSTEM_PROMPT"]
```

- [ ] **Step 5: Find what this breaks, before running the suite**

```bash
grep -rn 'core\.[a-zA-Z_]' homunculus/ tests/ scripts/ | grep -v 'core.Agent\|core.SYSTEM_PROMPT' | sort
```

Every line printed is an attribute the façade no longer has. Expect `core.MODEL`, `core.API_URL`, `core.get_config`, `core.tool_result_indicates_failure`, `core.READ_ONLY_CACHEABLE_TOOLS`, `core.DEFAULT_TOOL_TURN_CAPS`, `core.measure_llm_usage_since`. Repoint each at its real owner **in this task** — a façade that keeps re-exporting them is the accidental hub the spec is closing, just relocated.

- [ ] **Step 6: Verify**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
```

The characterisation suite from Phase 0 is the gate. If any of it fails, the move changed behaviour; `git diff` the specific phase rather than adjusting the test.

- [ ] **Step 7: Confirm the deployment still boots**

```bash
docker compose build web heartbeat && docker compose up -d web heartbeat
docker compose logs --tail=50 heartbeat
```

Expected: the startup audit runs and lists its checks, with no import error. A green suite does not prove the container's import graph resolves — the suite stubs `mcp` and `homunculus.tools`, and the container does not.

- [ ] **Step 8: Commit**

```bash
git checkout -b refactor/agent-loop-package
git add homunculus/ tests/
git commit -m "refactor: move the turn lifecycle into homunculus.agent

core.py becomes a façade over Agent and SYSTEM_PROMPT. Callers that
reached through it for MODEL, API_URL and friends now import the owner,
so deleting a line from core can no longer break a transport."
git push -u origin refactor/agent-loop-package && gh pr create --fill
```

---

### Task 11: Retire the façade

**Files:**
- Modify: every module importing `Agent` or `SYSTEM_PROMPT` from `homunculus.core`
- Delete: `homunculus/core.py`

**Interfaces:**
- Consumes: `homunculus.agent.Agent`, `homunculus.agent.prompt.SYSTEM_PROMPT`.
- Produces: no `homunculus/core.py`.

- [ ] **Step 1: Enumerate**

```bash
grep -rln 'homunculus.core\|from homunculus import core' homunculus/ tests/ scripts/
```

- [ ] **Step 2: Repoint**

```python
# before
from homunculus.core import Agent, SYSTEM_PROMPT
# after
from homunculus.agent import Agent
from homunculus.agent.prompt import SYSTEM_PROMPT
```

- [ ] **Step 3: Delete `core.py` and prove nothing references it**

```bash
git rm homunculus/core.py
grep -rn 'homunculus.core\|homunculus/core' homunculus/ tests/ scripts/ docs/ Dockerfile* docker-compose*.yml pyproject.toml
```

Expected: only historical references in `docs/`. Update `docs/CODE_REVIEW_2026_08_18.md` and `docs/CORE_REFACTOR_PLAN.md` with a one-line note that the file was retired here, and its successor. Do not rewrite their findings — they are dated records.

- [ ] **Step 4: Update `LEARN.md`**

The layer description for `core.py` becomes the `homunculus/agent/` package: name each module and the one question it answers. This is the reader's map; it is the reason the phase happened.

- [ ] **Step 5: Verify, redeploy, commit**

```bash
uv run python -m pytest -q && uv run ruff check homunculus tests scripts && uv run pyright homunculus
docker compose build web heartbeat && docker compose up -d web heartbeat
git checkout -b refactor/retire-core-facade
git add -A
git commit -m "refactor: retire homunculus/core.py

Every caller now imports from the module that owns what it needs."
git push -u origin refactor/retire-core-facade && gh pr create --fill
```

---

## Exit criteria for Phases 0 and 1

- [ ] `homunculus/core.py` no longer exists; no file in `homunculus/agent/` exceeds 400 lines.
- [ ] The characterisation suite passes unmodified from the commit that created it — verify with `git log --follow -p tests/test_characterisation_*.py` showing no assertion edits after Task 4.
- [ ] `grep -rn 'from homunculus.agent import _' homunculus/ tests/` returns nothing: no underscore-prefixed symbol crosses a module boundary.
- [ ] Test count is greater than or equal to the Phase 0 exit note's figure (which is ≥ the 1,436 baseline plus whatever Tasks 1-4 added).
- [ ] `uv run pyright homunculus` is clean.
- [ ] The container boots and the startup audit runs.
- [ ] `LEARN.md` describes the package as it now exists.

## What this plan does not do

- It does not unify the interception contract — that is Phase 2, and doing it here would mean the characterisation tests are being used to validate a behaviour change rather than to guard against one.
- It does not touch `heartbeat.py` or `llm.py`. Both get the same treatment, one PR each, after `core.py` is settled. The order is forced: `heartbeat` imports `Agent`.
- It does not remove dead code. `messages.py` and `skill_contracts.py` need a reviewed inventory first.
- It does not fix the `reasoning_effort` staleness, the timezone-localisation finding, or the pricing table. Those are Phases 4 and 5, and each needs its own sign-off.
