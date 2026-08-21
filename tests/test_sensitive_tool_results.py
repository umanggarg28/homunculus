"""A CV must not reach the event log.

`redact_secrets` matches credential prefixes, and a name, employer, or phone
number has no prefix to match — so it passes personal data through untouched.
The event log renders in the web console and screenshots of that console are
committed to a public repository, which makes the log the wrong place for the
career tools' output.

The scope is deliberately narrow: withheld from the LOG, never from the model.
"""

from homunculus.security import (
    SENSITIVE_RESULT_TOOLS,
    loggable_tool_result,
    redact_secrets,
)

CV = (
    "# Umang Garg — Career Context\n## Personal\nEmail: someone@example.com\n"
    "## Work History\n### PictorLabs.ai — Full Stack Developer 2\n"
)


def test_redact_secrets_does_not_touch_personal_data():
    """The premise of the fix: the existing scrubber cannot help here."""
    assert redact_secrets(CV) == CV


def test_career_context_result_is_withheld_from_the_log():
    out = loggable_tool_result("career_context", CV)
    assert "Umang" not in out
    assert "PictorLabs" not in out
    assert "example.com" not in out


def test_the_placeholder_keeps_the_trace_honest():
    """A withheld result must still show the call happened and its size —
    otherwise the trace lies by omission."""
    out = loggable_tool_result("career_context", CV)
    assert "career_context" in out
    assert f"{len(CV):,}" in out


def test_ordinary_tools_are_untouched():
    assert loggable_tool_result("web_fetch", "hello world") == "hello world"
    assert loggable_tool_result("notify", "sent") == "sent"


def test_job_posting_stays_visible():
    """A job advert is public text, and it is what makes an application trace
    debuggable. It is deliberately not in the set."""
    ad = "Senior Engineer at ExampleCorp — remote"
    assert "job_posting" not in SENSITIVE_RESULT_TOOLS
    assert loggable_tool_result("job_posting", ad) == ad


def test_the_whole_application_family_is_covered():
    for name in ("career_context", "prepare_application", "draft_all_answers", "draft_answer"):
        assert loggable_tool_result(name, CV) != CV


def test_non_string_results_do_not_crash():
    assert loggable_tool_result("web_fetch", 42) == "42"
    assert "withheld" in loggable_tool_result("career_context", {"cv": "x"})


# --- the second door: the llm_call request trace ---------------------------

def test_request_trace_withholds_a_sensitive_tool_message():
    """`_serialize_messages` dumps the last 6 messages into the llm_call
    event. Withholding the payload from the `tool_result` event alone left it
    sitting in the very next `llm_call` record — found by grepping the real
    log after the first fix, not by the suite."""
    from homunculus.llm import _serialize_messages

    out = _serialize_messages([
        {"role": "user", "content": "what is my title?"},
        {"role": "tool", "tool_call_id": "c1", "content": CV, "_tool": "career_context"},
    ])
    assert "PictorLabs" not in out
    assert "Umang" not in out
    assert "withheld from the log" in out


def test_request_trace_keeps_ordinary_tool_messages():
    from homunculus.llm import _serialize_messages

    out = _serialize_messages([
        {"role": "tool", "tool_call_id": "c1", "content": "sunny, 24C", "_tool": "get_weather"},
    ])
    assert "sunny, 24C" in out


def test_the_tool_tag_never_reaches_the_provider():
    """`_tool` is ours. A provider receiving an unknown key on a message is
    exactly the class of 400 that cross-provider fallback already cost us."""
    from homunculus.llm import _strip_internal_fields

    stripped = _strip_internal_fields([
        {"role": "tool", "tool_call_id": "c1", "content": "x", "_tool": "career_context"},
    ])
    assert stripped == [{"role": "tool", "tool_call_id": "c1", "content": "x"}]
