"""Repetition the exact-args stuck-loop cannot see.

`STUCK_LOOP` keys on (tool, serialized args), so a model that rewords its
request escapes it on every attempt — the counter restarts. That is the
weak model's actual production failure mode: `heartbeat` records 11
`remember()` calls in a single reflection tick, all paraphrases of one
summary, and the identity check fired on none of them.

The per-tool turn cap is argument-blind, so paraphrase cannot reset it. It
covers only durable-write tools: a turn that reads twenty files is working,
a turn that writes the same memory six times is thrashing.
"""

from __future__ import annotations

import json

import pytest

from homunculus import core
from homunculus.core import DEFAULT_TOOL_TURN_CAPS, Agent


def _call(name: str, **args) -> dict:
    return {
        "id": f"c-{name}-{hash(json.dumps(args, sort_keys=True)) & 0xFFFF}",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.fixture(autouse=True)
def _skip_schema_check(monkeypatch):
    """The conftest stub leaves the tool catalogue empty, so the loop's
    schema check would short-circuit every dispatch before the cap runs."""
    from homunculus import core

    monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)


@pytest.fixture
def agent():
    return Agent(memory=None)


def test_paraphrased_repetition_is_capped(agent, monkeypatch):
    """Six differently-worded remember() calls: the cap fires, identity can't."""
    executed: list[dict] = []

    def fake_execute(name, args):
        executed.append({"name": name, "args": args})
        return "stored"

    monkeypatch.setattr(core.tools, "execute", fake_execute, raising=False)

    per_tool: dict[str, int] = {}
    counts: dict = {}
    outcomes: list[dict] = []
    phrasings = [
        "Umang prefers uv over pip",
        "The user favours uv rather than pip",
        "uv is preferred to pip by the user",
        "Preference noted: uv, not pip",
        "User likes uv more than pip",
        "Noted that uv beats pip for this user",
    ]
    for text in phrasings:
        agent._dispatch_tool_calls(
            [_call("remember", text=text)], set(), counts, {}, outcomes, per_tool
        )

    cap = DEFAULT_TOOL_TURN_CAPS["remember"]
    assert per_tool["remember"] == len(phrasings)
    # Every phrasing is textually distinct, so the identity counter never
    # reached its own threshold — the cap is what stopped this.
    assert max(counts.values()) == 1
    blocked = [o for o in outcomes if "STUCK_LOOP" in str(o.get("result") or "")]
    assert len(blocked) == len(phrasings) - cap


def test_reads_are_not_capped(agent, monkeypatch):
    """Bulk reading is legitimate work and must stay uncapped."""
    monkeypatch.setattr(core.tools, "execute", lambda n, a: "file body", raising=False)
    per_tool: dict[str, int] = {}
    outcomes: list[dict] = []
    for i in range(12):
        agent._dispatch_tool_calls(
            [_call("read_file", path=f"notes/{i}.md")], set(), {}, {}, outcomes, per_tool
        )
    assert per_tool["read_file"] == 12
    assert not [o for o in outcomes if "STUCK_LOOP" in str(o.get("result") or "")]
    assert "read_file" not in DEFAULT_TOOL_TURN_CAPS


def test_caps_are_caller_overridable(monkeypatch):
    """Reflection ticks declare a stricter budget than a chat turn."""
    agent = Agent(memory=None, tool_turn_caps={"remember": 1})
    monkeypatch.setattr(core.tools, "execute", lambda n, a: "stored", raising=False)
    per_tool: dict[str, int] = {}
    outcomes: list[dict] = []
    for text in ("first thing", "a second, different thing"):
        agent._dispatch_tool_calls(
            [_call("remember", text=text)], set(), {}, {}, outcomes, per_tool
        )
    assert len([o for o in outcomes if "STUCK_LOOP" in str(o.get("result") or "")]) == 1


def test_default_caps_only_cover_durable_writes():
    """A guard against widening this table carelessly: capping a read tool
    would break legitimate multi-file work to fix a write-thrash problem."""
    reads = {"read_file", "search_files", "list_files", "recall", "web_fetch"}
    assert not (set(DEFAULT_TOOL_TURN_CAPS) & reads)
