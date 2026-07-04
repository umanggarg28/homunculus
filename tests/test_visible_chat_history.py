"""_visible_chat_history — the chat page must show CHAT, not agent guts.

Two live bugs (2026-06-11 screenshots):

1. LEAKAGE: heartbeat tick prompts and harness corrections ("Your last
   reply did not include a tool call...") rendered as YOU bubbles, and
   mid-run model text from heartbeat sessions rendered as AI bubbles —
   the shared transcript wasn't filtered by source.

2. DUPLICATES: every final reply appeared twice. _journal_append wrote
   the raw assistant reply and _journal_replace_last_content
   unconditionally appended an identical rewrite record next to it.
   The endpoint now collapses adjacent assistant records (keep-last,
   which also picks the corrected form when the guard DID rewrite),
   and core no longer journals no-op rewrites.
"""

from homunculus.transports.web_api import _visible_chat_history


def _user(content, source="web", idx_ts="t"):
    return {"role": "user", "content": content, "source": source, "ts": idx_ts}


def _assistant(content, source="web", **extra):
    return {"role": "assistant", "content": content, "source": source, **extra}


def test_normal_chat_turn_passes_through():
    out = _visible_chat_history([
        _user("set a reminder"),
        _assistant("Reminder set."),
    ])
    assert [(m["role"], m["content"]) for m in out] == [
        ("user", "set a reminder"),
        ("assistant", "Reminder set."),
    ]


def test_heartbeat_and_harness_messages_are_hidden():
    out = _visible_chat_history([
        _user("It's a scheduled heartbeat tick — no user is talking...", source="heartbeat"),
        _assistant("record_failure(task_id=...)", source="heartbeat"),
        _user("Your last reply did not include a tool call.", source="harness"),
        _assistant("refining skill...", source="refinement"),
        _user("real question", source="web"),
        _assistant("real answer", source="web"),
    ])
    assert [(m["role"], m["content"]) for m in out] == [
        ("user", "real question"),
        ("assistant", "real answer"),
    ]


def test_adjacent_rewrite_pair_keeps_the_later_record():
    """journal_append + replace_last produce adjacent records; the later
    one is the final (possibly guard-corrected) form."""
    out = _visible_chat_history([
        _user("hi"),
        _assistant("raw reply with leaked path memory/feedback_x.md"),
        _assistant("cleaned reply"),
    ])
    assert [(m["role"], m["content"]) for m in out] == [
        ("user", "hi"),
        ("assistant", "cleaned reply"),
    ]


def test_legacy_identical_duplicates_collapse():
    out = _visible_chat_history([
        _user("set it"),
        _assistant("Reminder set and sent."),
        _assistant("Reminder set and sent."),
    ])
    assert [m["content"] for m in out if m["role"] == "assistant"] == [
        "Reminder set and sent.",
    ]


def test_non_adjacent_assistant_messages_are_distinct_turns():
    """Assistant replies separated by other records (tool results, user
    messages) are separate turns — never collapsed."""
    history = [
        _user("first"),
        _assistant("answer one"),
        {"role": "tool", "content": "tool output"},
        _assistant("answer two"),
    ]
    out = _visible_chat_history(history)
    assert [m["content"] for m in out if m["role"] == "assistant"] == [
        "answer one",
        "answer two",
    ]


def test_tool_call_planning_messages_and_notifications_skipped():
    out = _visible_chat_history([
        _user("do it"),
        _assistant("calling tool", tool_calls=[{"id": "x"}]),
        _assistant("[notification I sent you at 09:00]\n\nDaily problem..."),
        _assistant("done."),
    ])
    assert [m["content"] for m in out if m["role"] == "assistant"] == ["done."]


def test_internal_raw_idx_not_exposed():
    out = _visible_chat_history([_user("q"), _assistant("a")])
    assert all("_raw_idx" not in m for m in out)


def _tool_call(name):
    return {"id": "call_x", "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_tool_names_attach_to_the_final_reply():
    """The reply entry carries a `tools` receipt of what the agent
    actually called this turn — the chat UI's evidence line."""
    out = _visible_chat_history([
        _user("remind me to apply"),
        _assistant("", tool_calls=[_tool_call("create_task")]),
        {"role": "tool", "content": "Created task", "tool_call_id": "call_x"},
        _assistant("planning…", tool_calls=[_tool_call("update_task"), _tool_call("notify")]),
        {"role": "tool", "content": "ok", "tool_call_id": "call_x"},
        _assistant("Task created — I'll remind you at noon."),
    ])
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[1]["tools"] == ["create_task", "update_task", "notify"]


def test_tools_do_not_leak_across_turns():
    out = _visible_chat_history([
        _user("first"),
        _assistant("", tool_calls=[_tool_call("web_search")]),
        {"role": "tool", "content": "results", "tool_call_id": "call_x"},
        _assistant("Found it."),
        _user("second — no tools this time"),
        _assistant("Plain answer."),
    ])
    assert out[1]["tools"] == ["web_search"]
    assert "tools" not in out[3]


def test_heartbeat_tool_calls_never_count_as_chat_receipts():
    out = _visible_chat_history([
        _assistant("", source="heartbeat", tool_calls=[_tool_call("notify")]),
        _user("hello"),
        _assistant("Hi."),
    ])
    assert "tools" not in out[1]


def test_guard_rewrite_pair_keeps_the_receipt():
    """The adjacent rewrite record replaces the raw reply — the tools
    collected for the turn must survive the swap."""
    out = _visible_chat_history([
        _user("q"),
        _assistant("", tool_calls=[_tool_call("weather")]),
        {"role": "tool", "content": "SUNNY", "tool_call_id": "call_x"},
        _assistant("raw reply"),
        _assistant("guard-rewritten reply"),
    ])
    assert [m["content"] for m in out] == ["q", "guard-rewritten reply"]
    assert out[1]["tools"] == ["weather"]
