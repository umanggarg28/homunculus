"""System prompt must explicitly forbid the 'I don't have that tool' lie.

Round-5 stress probes 24, 43, 45: when asked to do something benign
(create a reminder, search the web), the agent refused by claiming the
tool didn't exist — even though it was registered. Pure dishonesty,
and it confuses the user about what's actually possible.

The structural fix is a system-prompt directive plus an unambiguous
statement of which tools ARE available. This pattern matches Letta's
'be explicit about your capabilities' framing and Anthropic's
guidance to sharpen refusal language.

Pin the directive so a prompt refactor can't quietly drop it.
"""

from __future__ import annotations


def test_prompt_forbids_false_tool_unavailable_refusals():
    from homunculus.core import SYSTEM_PROMPT
    lower = SYSTEM_PROMPT.lower()
    assert "never claim a tool" in lower or "do not claim a tool" in lower
    # Must list at least the most common tools the agent has lied about
    # being unable to use (create_task, web_search, web_fetch).
    for tool in ("create_task", "web_search", "web_fetch", "recall", "remember"):
        assert tool in SYSTEM_PROMPT, f"prompt should name {tool} so the model can't claim ignorance"


def test_prompt_says_create_task_handles_one_shot_reminders():
    """Round-5 probe 45: 'remind me at 8pm' was refused because the old
    prompt only mentioned recurring commitments. Pin the clarification."""
    from homunculus.core import SYSTEM_PROMPT
    lower = SYSTEM_PROMPT.lower()
    assert "one-shot" in lower or "remind me" in lower
    assert 'recurrence="none"' in SYSTEM_PROMPT or "recurrence='none'" in SYSTEM_PROMPT


def test_prompt_specifies_decline_format():
    """If the agent IS going to refuse, it must do so with a reason —
    not by claiming the tool doesn't exist."""
    from homunculus.core import SYSTEM_PROMPT
    lower = SYSTEM_PROMPT.lower()
    # The "I'm not going to do that because Y" template is the anchor.
    assert "i'm not going to" in lower or "i am not going to" in lower
