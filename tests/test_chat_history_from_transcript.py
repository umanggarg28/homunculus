"""/api/chat/history reads from the Transcript (PR #112 cutover).

Replaces the old _chat_log.jsonl test (deleted in this PR). The
visible-chat filter logic moved from the chat_log path onto the
transcript path; this file pins the new behavior end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from homunculus.memory import Memory
from homunculus.transcript import Transcript
from homunculus.transports.web_api import _visible_chat_history


def _append_msgs(t: Transcript, msgs: list[dict]) -> None:
    for m in msgs:
        t.append(m)


def test_visible_chat_history_emits_tx_id_when_marked(tmp_path: Path) -> None:
    """If callers tag messages with _tx_id (transcript path does this),
    the filter passes it back as _source_tx_id for stable IDs."""
    msgs = [
        {"role": "user", "content": "hi", "_tx_id": "000001"},
        {"role": "assistant", "content": "hello", "_tx_id": "000002"},
    ]
    out = _visible_chat_history(msgs)
    assert len(out) == 2
    assert out[0]["_source_tx_id"] == "000001"
    assert out[1]["_source_tx_id"] == "000002"


def test_visible_chat_history_drops_intermediate_tool_planning(tmp_path: Path) -> None:
    """Assistant messages with tool_calls are intermediate planning steps
    — they must NOT appear as user-visible chat bubbles."""
    msgs = [
        {"role": "user", "content": "do thing"},
        {"role": "assistant", "content": "Let me check.", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "assistant", "content": "Done."},
    ]
    out = _visible_chat_history(msgs)
    assert [m["content"] for m in out] == ["do thing", "Done."]


def test_visible_chat_history_skips_heartbeat_notifications(tmp_path: Path) -> None:
    """Heartbeat-injected notification echoes live in LLM context for
    follow-up questions but must NOT render as chat bubbles."""
    msgs = [
        {"role": "user", "content": "[notification I sent you at 09:00: hello]"},
        {"role": "user", "content": "real user question"},
        {"role": "assistant", "content": "answer"},
    ]
    out = _visible_chat_history(msgs)
    assert [m["content"] for m in out] == ["real user question", "answer"]


def test_visible_chat_history_skips_blank_content(tmp_path: Path) -> None:
    msgs = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "   "},
        {"role": "user", "content": "real"},
        {"role": "assistant", "content": "reply"},
    ]
    out = _visible_chat_history(msgs)
    assert [m["content"] for m in out] == ["real", "reply"]


def test_visible_chat_history_drops_orphan_user_without_assistant(tmp_path: Path) -> None:
    """A pending user message without a paired assistant final reply
    should NOT be emitted (it's an in-flight turn)."""
    msgs = [
        {"role": "user", "content": "first complete"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "second in-flight"},
    ]
    out = _visible_chat_history(msgs)
    assert [m["content"] for m in out] == ["first complete", "first reply"]


def test_memory_clear_transcript_removes_file(tmp_path: Path) -> None:
    """clear_transcript replaces the old clear_chat_log entry point.
    Chapter close calls this so a new chapter starts with empty record."""
    mem = Memory(tmp_path)
    t = Transcript(mem.transcript_path)
    t.append({"role": "user", "content": "doomed"})
    assert mem.transcript_path.exists()
    mem.clear_transcript()
    assert not mem.transcript_path.exists()


def test_memory_transcript_path_points_at_underscore_file(tmp_path: Path) -> None:
    """Stable path contract — the heartbeat container relies on this
    being inside the memory dir, not a sibling. Pin it."""
    mem = Memory(tmp_path)
    assert mem.transcript_path == tmp_path / "_transcript.jsonl"


def test_full_round_trip_transcript_to_visible_chat(tmp_path: Path) -> None:
    """End-to-end: append to transcript with tx IDs, run filter, get
    visible chat entries with stable tx-prefixed IDs."""
    mem = Memory(tmp_path)
    t = Transcript(mem.transcript_path)
    t.append({"role": "user", "content": "ping"})
    t.append({"role": "assistant", "content": "Working on it.", "tool_calls": [{"id": "c1"}]})
    t.append({"role": "tool", "tool_call_id": "c1", "content": "result"})
    t.append({"role": "assistant", "content": "pong"})

    records = t.all()
    msgs_with_ids = [{**msg, "_tx_id": rid} for rid, msg in records]
    out = _visible_chat_history(msgs_with_ids)

    assert [m["content"] for m in out] == ["ping", "pong"]
    assert out[0]["_source_tx_id"] == "000001"
    assert out[1]["_source_tx_id"] == "000004"
