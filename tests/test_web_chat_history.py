import importlib
import sys
import types


def _load_web_api():
    if "tools" not in sys.modules:
        tools_stub = types.ModuleType("tools")
        tools_stub.SCHEMAS = []
        tools_stub.init = lambda *a, **k: None
        tools_stub.get_mode = lambda: "build"
        tools_stub.set_mode = lambda mode: None
        sys.modules["tools"] = tools_stub
    return importlib.import_module("transports.web_api")


def test_visible_chat_history_drops_orphan_user_turns():
    web_api = _load_web_api()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        {"role": "user", "content": "second"},
    ]

    assert web_api._visible_chat_history(history) == []


def test_visible_chat_history_keeps_complete_visible_pairs():
    web_api = _load_web_api()
    history = [
        {"role": "user", "content": "what are my goals?"},
        {"role": "assistant", "content": "leetcode and job applications"},
    ]

    assert web_api._visible_chat_history(history) == [
        {"id": "persisted-0", "role": "user", "content": "what are my goals?"},
        {
            "id": "persisted-1",
            "role": "assistant",
            "content": "leetcode and job applications",
        },
    ]


def test_visible_chat_history_hides_heartbeat_notifications():
    web_api = _load_web_api()
    history = [
        {"role": "assistant", "content": "[notification I sent you at 09:00]\n\nDone"},
        {"role": "user", "content": "explain that"},
        {"role": "assistant", "content": "I notified you about the task."},
    ]

    assert web_api._visible_chat_history(history) == [
        {"id": "persisted-1", "role": "user", "content": "explain that"},
        {
            "id": "persisted-2",
            "role": "assistant",
            "content": "I notified you about the task.",
        },
    ]


def test_visible_chat_history_hides_contentful_tool_planning_messages():
    web_api = _load_web_api()
    history = [
        {"role": "user", "content": "save a comparison table"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "search-1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "search-1", "content": "results"},
        {
            "role": "assistant",
            "content": "Here is a table; I will save it now.",
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "write-1", "content": "Wrote file"},
        {"role": "assistant", "content": "Saved the table to notes/frameworks.md."},
    ]

    assert web_api._visible_chat_history(history) == [
        {"id": "persisted-0", "role": "user", "content": "save a comparison table"},
        {
            "id": "persisted-5",
            "role": "assistant",
            "content": "Saved the table to notes/frameworks.md.",
        },
    ]
