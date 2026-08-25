"""Two costs the loop was paying every day, both structural.

`no_action` — the reflection prompt says "if the delivery is genuinely good →
no action", and the turn runs under tool_choice=required. There was no tool
that means "nothing to do", so a correct review had no legal move: the model
answered in prose, the harness recorded `required_tool_violation` twice, and
the turn died. Observed on three consecutive days, with the model's prose
reading "no skill edits proposed — all 7 deliveries were genuinely good".

`load_tool` batching — one tool per call, and each call costs a whole model
round-trip before any work happens. It was the most-called tool in
production (98 calls, ahead of read_file's 63) against a $5/month ceiling.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import load_real_tool_submodule

_TOOLS = Path(__file__).resolve().parent.parent / "homunculus" / "tools"

_meta = load_real_tool_submodule("_meta")

#: Read from source rather than imported: the package __init__ pulls in the
#: MCP stack, which is container-only.
ALWAYS_LOADED = {
    line.strip().strip('",')
    for line in (_TOOLS / "__init__.py").read_text()
    .split("ALWAYS_LOADED: frozenset[str] = frozenset({", 1)[1]
    .split("})", 1)[0]
    .splitlines()
    if line.strip().startswith('"')
}


class TestNoAction:
    def test_is_always_loaded(self):
        """It must never require a load_tool call to reach: a forced turn
        cannot spend its one tool call on loading the escape hatch."""
        assert "no_action" in ALWAYS_LOADED

    def test_records_the_reason(self):
        out = _meta.no_action("Reviewed all 7 deliveries; every one grounded.")
        assert "No action taken" in out
        assert "7 deliveries" in out

    def test_refuses_an_unexplained_no_op(self):
        """Without a reason it is indistinguishable from giving up."""
        assert _meta.no_action("").startswith("ERROR")
        assert _meta.no_action("   ").startswith("ERROR")


class TestLoadToolBatching:
    def test_single_name_still_works(self):
        """The existing calling convention must not break."""
        out = _meta.load_tool("get_weather")
        assert "get_weather" in out and "ERROR" not in out

    def test_list_loads_several_at_once(self):
        out = _meta.load_tool(["get_weather", "news_headlines"])
        assert "get_weather" in out and "news_headlines" in out
        assert "ERROR" not in out

    def test_empty_request_is_refused(self):
        assert _meta.load_tool([]).startswith("ERROR")
        assert _meta.load_tool("  ").startswith("ERROR")

    def test_unknown_names_are_named_back(self, monkeypatch):
        monkeypatch.setattr(
            _meta._state, "get_known_tool_names",
            lambda: {"get_weather"}, raising=False,
        )
        out = _meta.load_tool(["get_weather", "not_a_tool"])
        assert out.startswith("ERROR") and "not_a_tool" in out
        # The valid one is not silently loaded behind a failure.
        assert "Loaded" not in out


class TestLoopActivatesBatchedTools:
    def test_a_list_activates_every_named_tool(self, monkeypatch):
        """The Agent, not the tool function, owns the active-set mutation."""
        from homunculus import core

        # The suite-wide conftest stub leaves SCHEMAS empty, so the loop's
        # schema check would reject every call before dispatch. This test is
        # about the activation step that follows it.
        monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)
        agent = core.Agent(memory=None)
        agent._active_tool_names = set()
        monkeypatch.setattr(
            core.tools, "tool_names",
            lambda: {"get_weather", "news_headlines", "task_health_summary"},
            raising=False,
        )
        monkeypatch.setattr(core.tools, "execute", lambda n, a: "ok", raising=False)

        import json
        agent._dispatch_tool_calls(
            [{
                "id": "c1",
                "function": {
                    "name": "load_tool",
                    "arguments": json.dumps(
                        {"name": ["get_weather", "news_headlines"]}
                    ),
                },
            }],
            set(), {}, {}, [],
        )
        assert {"get_weather", "news_headlines"} <= agent._active_tool_names

    def test_a_bare_string_still_activates(self, monkeypatch):
        from homunculus import core

        monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)
        agent = core.Agent(memory=None)
        agent._active_tool_names = set()
        monkeypatch.setattr(
            core.tools, "tool_names", lambda: {"get_weather"}, raising=False
        )
        monkeypatch.setattr(core.tools, "execute", lambda n, a: "ok", raising=False)

        import json
        agent._dispatch_tool_calls(
            [{
                "id": "c1",
                "function": {
                    "name": "load_tool",
                    "arguments": json.dumps({"name": "get_weather"}),
                },
            }],
            set(), {}, {}, [],
        )
        assert "get_weather" in agent._active_tool_names


class TestNoActionTerminatesTheTurn:
    """An escape hatch that does not open is a loop.

    Shipping `no_action` without ending the turn traded one failure for
    another: under tool_choice=required the model declared "nothing further to
    do", the loop demanded another call, and the only honest answer available
    was the same one again. Observed live on 2026-08-22 — fourteen no_action
    calls in one reflection tick, eight of them inside a single minute.
    """

    def test_it_is_capped_at_one_call_per_turn(self):
        from homunculus.core import DEFAULT_TOOL_TURN_CAPS

        assert DEFAULT_TOOL_TURN_CAPS.get("no_action") == 1

    def test_a_second_call_is_refused(self, monkeypatch):
        import json

        from homunculus import core

        monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)
        monkeypatch.setattr(core.tools, "execute", lambda n, a: "noted", raising=False)
        agent = core.Agent(memory=None)
        per_tool: dict[str, int] = {}
        outcomes: list[dict] = []
        for reason in ("nothing to do", "still nothing to do, rephrased"):
            agent._dispatch_tool_calls(
                [{"id": "c", "function": {
                    "name": "no_action",
                    "arguments": json.dumps({"reason": reason}),
                }}],
                set(), {}, {}, outcomes, per_tool,
            )
        blocked = [o for o in outcomes if "STUCK_LOOP" in str(o.get("result") or "")]
        assert len(blocked) == 1, "the second declaration must be refused"

    def test_the_loop_exits_on_it(self):
        """The primary mechanism: the turn ends, so a second call never arises."""
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "homunculus" / "core.py").read_text()
        assert 'if "no_action" in tool_names_used:' in src
        exit_at = src.index('if "no_action" in tool_names_used:')
        # It must precede the due-task early exit, which cannot fire for a
        # reflection tick (no due tasks means expected_completions is None).
        assert exit_at < src.index("expected_completions is not None")
