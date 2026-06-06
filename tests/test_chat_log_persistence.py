"""Chat log persistence — independent of LLM-context compaction.

The headline bug: mid-session compaction rewrites self.history to a
summary + recent tail for LLM efficiency. Before this change, the same
compacted self.history was written to _session.json and read back by
/api/chat/history — so the user's chat would visibly lose its older
turns every ~5 messages. Now turns go to an append-only _chat_log.jsonl
that compaction never touches.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_append_and_load_chat_log(tmp_path: Path) -> None:
    from memory import Memory
    mem = Memory(tmp_path)
    mem.append_chat_turn({"role": "user", "content": "first", "ts": "t1"})
    mem.append_chat_turn({"role": "assistant", "content": "reply1", "ts": "t2"})
    mem.append_chat_turn({"role": "user", "content": "second", "ts": "t3"})

    log = mem.load_chat_log()
    assert [m["content"] for m in log] == ["first", "reply1", "second"]
    assert [m["role"] for m in log] == ["user", "assistant", "user"]


def test_compaction_of_session_does_not_truncate_chat_log(tmp_path: Path) -> None:
    """The session file may be compacted (rewritten with a summary +
    tail) but the chat log must keep every turn."""
    from memory import Memory
    mem = Memory(tmp_path)

    # Simulate 12 turns going through chat_send finally block: each
    # turn appends to the log and rewrites the session with the
    # current (possibly compacted) history.
    for i in range(12):
        mem.append_chat_turn({"role": "user", "content": f"u{i}", "ts": f"t{i}u"})
        mem.append_chat_turn({"role": "assistant", "content": f"a{i}", "ts": f"t{i}a"})

    # Now simulate compaction: session gets rewritten with a summary +
    # last 4 turns. The user-visible log must remain untouched.
    compacted = [
        {"role": "system", "content": "# Summary of earlier conversation\n..."},
        {"role": "user", "content": "u10"},
        {"role": "assistant", "content": "a10"},
        {"role": "user", "content": "u11"},
        {"role": "assistant", "content": "a11"},
    ]
    mem.save_session(compacted)

    log = mem.load_chat_log()
    # All 24 original turns must still be present in the chat log.
    assert len(log) == 24
    assert log[0]["content"] == "u0"
    assert log[-1]["content"] == "a11"

    # And the session file (LLM-context) reflects the compacted form
    # (system summary is filtered by save_session — only the 4 visible
    # tail turns remain in the persisted session).
    assert len(mem.load_session()) == 4


def test_load_chat_log_skips_malformed_lines(tmp_path: Path) -> None:
    from memory import Memory
    mem = Memory(tmp_path)
    mem.append_chat_turn({"role": "user", "content": "good"})
    # Hand-write a malformed trailing fragment (simulates a partial
    # write at shutdown).
    with mem.chat_log_path.open("a", encoding="utf-8") as f:
        f.write('{"role":"user","con')  # truncated JSON, no newline
    log = mem.load_chat_log()
    assert len(log) == 1
    assert log[0]["content"] == "good"


def test_clear_chat_log_only_clears_log(tmp_path: Path) -> None:
    """chapter_close calls both clear_session and clear_chat_log; they
    must be independent so each can be invoked alone in tests."""
    from memory import Memory
    mem = Memory(tmp_path)
    mem.append_chat_turn({"role": "user", "content": "x"})
    mem.save_session([{"role": "user", "content": "x"}])
    mem.clear_chat_log()
    assert not mem.chat_log_path.exists()
    assert mem.session_path.exists()
    assert mem.load_chat_log() == []


def test_chat_log_unicode_roundtrip(tmp_path: Path) -> None:
    """Non-ASCII content must survive a round trip without escaping."""
    from memory import Memory
    mem = Memory(tmp_path)
    mem.append_chat_turn({"role": "user", "content": "नमस्ते 🎉 こんにちは"})
    log = mem.load_chat_log()
    assert log[0]["content"] == "नमस्ते 🎉 こんにちは"
