"""A tool_call must not carry one provider's dialect to another.

Both directions were observed live on 2026-08-17, on the same task:

  * Gemini decorates calls with `extra_content.google.thought_signature`.
    Replayed to OpenRouter → 400 "property ... extra_content is unsupported".
  * A call made without that signature, sent to Gemini → 400 "Function call
    is missing a thought_signature in functionCall parts".

One turn falling over to a second provider poisons the accumulated history
for somebody no matter where it is sent next.
"""

import homunculus.llm as llm

GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


def _gemini_style_message() -> dict:
    return {
        "role": "assistant",
        "_origin": "generativelanguage.googleapis.com",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_tasks", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "El4KXAERTTIPohD8"}},
        }],
    }


def test_foreign_decoration_is_stripped():
    out = llm._sanitize_tool_calls_for([_gemini_style_message()], OPENROUTER)
    call = out[0]["tool_calls"][0]
    assert "extra_content" not in call
    assert call == {"id": "call_1", "type": "function",
                    "function": {"name": "list_tasks", "arguments": "{}"}}


def test_decoration_survives_a_return_to_its_own_provider():
    """Gemini REQUIRES its own signature back — stripping unconditionally
    would trade one 400 for the other."""
    out = llm._sanitize_tool_calls_for([_gemini_style_message()], GEMINI)
    assert "extra_content" in out[0]["tool_calls"][0]


def test_message_without_origin_is_treated_as_foreign():
    """Restored from disk, or harness-written: no origin means we cannot know
    it is safe, and a stripped call is universally valid."""
    msg = _gemini_style_message()
    del msg["_origin"]
    out = llm._sanitize_tool_calls_for([msg], GEMINI)
    assert "extra_content" not in out[0]["tool_calls"][0]


def test_sanitizing_does_not_mutate_the_caller_history():
    msg = _gemini_style_message()
    llm._sanitize_tool_calls_for([msg], OPENROUTER)
    assert "extra_content" in msg["tool_calls"][0]


def test_messages_without_tool_calls_pass_through_untouched():
    msgs = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "ok"}]
    assert llm._sanitize_tool_calls_for(msgs, OPENROUTER) == msgs


def test_origin_never_reaches_the_request_body():
    payload = llm._build_payload(
        [_gemini_style_message()], None, OPENROUTER, "m", "auto", "low", None,
    )
    assert all("_origin" not in m for m in payload["messages"])


def test_build_payload_sanitizes_end_to_end():
    payload = llm._build_payload(
        [_gemini_style_message()], None, OPENROUTER, "m", "auto", "low", None,
    )
    assert "extra_content" not in payload["messages"][0]["tool_calls"][0]
