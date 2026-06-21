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
