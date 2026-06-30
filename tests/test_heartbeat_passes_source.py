"""heartbeat.tick must pass source='heartbeat' to agent.chat.

Regression: heartbeat.py:496 was `agent.chat(prompt)` — the source kwarg
defaulted to "web", which routed _run_loop down the chat path with
tool_choice="auto". The entire PR #119 force-required machinery was a
no-op for heartbeat ticks for weeks. Symptom in prod 2026-06-09: the
LeetCode tick model freely returned prose containing the solution in
assistant_reply, never called notify() or complete_task() — silent drop.

This test pins the wiring: any heartbeat tick that engages the agent
must forward source="heartbeat" so the loop hits the required-tool
branch (tool_choice="required" + defense-in-depth detector + retry).
"""

from __future__ import annotations

from pathlib import Path


def _heartbeat_source() -> str:
    """Return the full source of heartbeat.py. We read the file directly
    instead of importing it, because the conftest `tools` stub doesn't expose
    tools.notify and importing heartbeat explodes at module load.

    The check spans the whole module (not just `tick`) because the agent.chat
    calls live in the helpers `tick` delegates to (`_run_reflection_or_idle`,
    `_run_task_isolated`) — the source='heartbeat' invariant must hold at every
    call site no matter how the orchestration is factored."""
    return (Path(__file__).parent.parent / "homunculus" / "heartbeat.py").read_text()


def test_heartbeat_tick_calls_agent_chat_with_source_heartbeat():
    """Static check: every agent.chat( call in heartbeat.py must pass
    source='heartbeat'. A textual check is the simplest durable guarantee —
    mocking the full tick requires too much setup and would drift as task/guard
    wiring changes."""
    src = _heartbeat_source()
    # Find every agent.chat( occurrence and confirm source="heartbeat"
    # is in the same call. Skip matches that sit on a comment line —
    # references like "# agent.chat() returned without an exception"
    # describe the call, they aren't the call.
    import re
    lines = src.splitlines()
    line_starts: list[int] = [0]
    for line in lines[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)

    def _line_at(offset: int) -> str:
        # Locate which line `offset` falls on via binary-search style
        # scan over the cumulative starts.
        i = max(j for j, s in enumerate(line_starts) if s <= offset)
        return lines[i]

    chat_calls = [
        m.start() for m in re.finditer(r"agent\.chat\(", src)
        if not _line_at(m.start()).lstrip().startswith("#")
    ]
    assert chat_calls, "heartbeat.py must call agent.chat somewhere"
    for pos in chat_calls:
        # Window the next ~200 chars to find the matching close paren
        # — covers multi-line call shapes. The literal source="heartbeat"
        # must appear inside that window.
        window = src[pos:pos + 400]
        assert 'source="heartbeat"' in window or "source='heartbeat'" in window, (
            f"agent.chat( at offset {pos} in heartbeat.tick is missing "
            f"source='heartbeat'. Required by PR #119 so the loop "
            f"forces tool_choice=required and the detector can fire. "
            f"Without it, the model is free to return prose and silent-"
            f"drop the task. Call site window:\n\n{window[:300]}"
        )
