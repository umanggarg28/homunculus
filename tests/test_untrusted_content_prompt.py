"""The system prompt must include an explicit untrusted-content clause.

Indirect prompt injection is when adversarial instructions live inside
content the agent fetches — a memory file, a web page, a tool result.
Without a system prompt clause naming this threat model, the agent will
treat fetched instructions as if they came from the user.

Anthropic's prompt-injection mitigation guide makes this the first line
of defense; Pi / Letta / Cognee all carry a similar clause in their
defaults. We pin the presence of the policy here so a future prompt
refactor can't accidentally drop it.
"""

from __future__ import annotations


def test_system_prompt_names_the_threat_model():
    from core import SYSTEM_PROMPT
    lower = SYSTEM_PROMPT.lower()
    # The clause must (a) call the data untrusted and (b) name at least
    # one of the content-fetching tools so the model has a concrete
    # anchor.
    assert "untrusted" in lower
    # Both read_file and recall should be named — those are the two
    # everyday content-fetch surfaces.
    assert "read_file" in lower
    assert "recall" in lower
    # The instruction must be unambiguous about NOT executing fetched
    # instructions.
    assert "override" in lower or "cannot override" in lower or "never let" in lower


def test_system_prompt_tells_agent_how_to_report_an_injection_attempt():
    """The clause should not just say 'don't' — it should give the agent
    a concrete behavior for the case where it does encounter an
    injection: report it to the user."""
    from core import SYSTEM_PROMPT
    lower = SYSTEM_PROMPT.lower()
    assert "summarise that fact" in lower or "report" in lower or "tell the user" in lower
