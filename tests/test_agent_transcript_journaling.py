"""Agent journals every real message append into the Transcript.

PR #111 wires Agent to mirror self.history mutations into the new
Transcript. Reads still come from self.history — the transcript is a
passive observer this turn. PR #112 cuts over the chat history endpoint.

These tests use the Agent's helpers directly (no LLM, no provider) so
they're fast and don't depend on the loop machinery. The integration is
exercised end-to-end by the existing test_compaction / test_eviction
suites which still pass.
"""

from __future__ import annotations

from pathlib import Path

from homunculus.core import Agent
from homunculus.memory import Memory
from homunculus.transcript import Transcript


def _agent(tmp_path: Path) -> Agent:
    """Build an Agent backed by a fresh Memory dir at tmp_path."""
    mem = Memory(tmp_path)
    return Agent(memory=mem)


# ---- journaled appends ------------------------------------------------


def test_journal_append_writes_to_transcript(tmp_path: Path) -> None:
    a = _agent(tmp_path)
    a._journal_append({"role": "user", "content": "hello"})
    assert a._message_ids == ["000001"]
    on_disk = Transcript(tmp_path / "_transcript.jsonl").all()
    assert on_disk == [("000001", {"role": "user", "content": "hello"})]


def test_journal_append_also_appends_to_history(tmp_path: Path) -> None:
    a = _agent(tmp_path)
    initial_len = len(a.history)
    a._journal_append({"role": "user", "content": "x"})
    assert len(a.history) == initial_len + 1
    assert a.history[-1] == {"role": "user", "content": "x"}


def test_message_ids_track_history_one_to_one(tmp_path: Path) -> None:
    """Every journaled append must add exactly one id, never drift."""
    a = _agent(tmp_path)
    for i in range(5):
        a._journal_append({"role": "user", "content": f"m{i}"})
    # history = [system, m0, m1, m2, m3, m4] = 6
    assert len(a.history) == 6
    assert len(a._message_ids) == 5


# ---- in-place reply rewrite -------------------------------------------


def test_journal_replace_last_content_rewrites_and_logs_both(tmp_path: Path) -> None:
    """Self-correction rewrites the last assistant content. The transcript
    gets a NEW record for the rewritten version; the pre-edit record
    stays on disk as evidence."""
    a = _agent(tmp_path)
    a._journal_append({"role": "assistant", "content": "first attempt"})
    a._journal_replace_last_content("corrected reply")

    # history sees the corrected version
    assert a.history[-1]["content"] == "corrected reply"
    # message_ids has 1 entry, pointing at the corrected (newer) record
    assert len(a._message_ids) == 1
    assert a._message_ids[0] == "000002"
    # transcript has BOTH records on disk
    on_disk = Transcript(tmp_path / "_transcript.jsonl").all()
    assert len(on_disk) == 2
    assert on_disk[0][1]["content"] == "first attempt"
    assert on_disk[1][1]["content"] == "corrected reply"


def test_journal_replace_last_on_empty_history_is_noop(tmp_path: Path) -> None:
    """Defensive: replacing before any append must not crash."""
    a = _agent(tmp_path)
    initial_len = len(a.history)
    # Synthetic empty: agents always have the system prompt, so this
    # is a contrived state — assert it just doesn't blow up.
    a._journal_replace_last_content("anything")
    assert len(a.history) == initial_len


# ---- compaction pointer rewrite ---------------------------------------


def test_rebuild_pointer_list_after_compaction(tmp_path: Path) -> None:
    """After compaction, message_ids = [summary_id, *tail_ids_kept].
    The pre-summary IDs are dropped from the pointer list but remain
    on disk in the transcript."""
    a = _agent(tmp_path)
    # Build 5 journaled messages — IDs 000001 .. 000005
    for i in range(5):
        a._journal_append({"role": "user", "content": f"m{i}"})

    pre_ids = list(a._message_ids)
    assert pre_ids == ["000001", "000002", "000003", "000004", "000005"]

    # Simulate compaction keeping only the last 2 messages.
    # history layout right now: [system, m0, m1, m2, m3, m4] — cut_at=4
    # keeps [m3, m4] as tail.
    kept_tail = a.history[4:]
    summary_msg = {"role": "system", "content": "summary text"}
    a.history = [a.history[0], summary_msg] + kept_tail
    a._rebuild_message_ids_after_compaction(summary_msg, kept_tail, cut_at=4)

    # Pointer list: [summary, m3, m4]
    assert len(a._message_ids) == 3
    assert a._message_ids[0] == "000006"  # summary is newest in transcript
    assert a._message_ids[1:] == ["000004", "000005"]

    # On-disk transcript still has ALL six records — nothing was deleted
    on_disk = Transcript(tmp_path / "_transcript.jsonl").all()
    assert len(on_disk) == 6
    assert [r[0] for r in on_disk] == [
        "000001", "000002", "000003", "000004", "000005", "000006",
    ]


# ---- lifecycle --------------------------------------------------------


def test_reset_clears_transcript_and_pointers(tmp_path: Path) -> None:
    a = _agent(tmp_path)
    a._journal_append({"role": "user", "content": "alpha"})
    a._journal_append({"role": "user", "content": "beta"})
    assert (tmp_path / "_transcript.jsonl").exists()

    a.reset()

    assert a._message_ids == []
    assert not (tmp_path / "_transcript.jsonl").exists()
    # System message is still there
    assert len(a.history) == 1
    assert a.history[0]["role"] == "system"


def test_restore_session_migrates_existing_session_into_transcript(tmp_path: Path) -> None:
    """Migration path: if _session.json exists but _transcript.jsonl
    doesn't (first run after this PR lands), the saved session is
    replayed into the transcript so the canonical record is complete."""
    mem = Memory(tmp_path)
    mem.save_session([
        {"role": "user", "content": "old message 1"},
        {"role": "assistant", "content": "old reply 1"},
        {"role": "user", "content": "old message 2"},
    ])
    assert not (tmp_path / "_transcript.jsonl").exists()

    a = Agent(memory=mem)
    restored = a.restore_session()

    assert restored == 3
    assert a._message_ids == ["000001", "000002", "000003"]
    on_disk = Transcript(tmp_path / "_transcript.jsonl").all()
    assert [r[1]["content"] for r in on_disk] == [
        "old message 1", "old reply 1", "old message 2",
    ]


def test_restore_session_with_existing_transcript_uses_tail_ids(tmp_path: Path) -> None:
    """When the transcript already has content (returning agent), the
    pointer list is seeded from the last N IDs — we don't backfill again."""
    mem = Memory(tmp_path)
    # Pre-seed the transcript as if a prior run journaled 4 messages.
    pre = Transcript(tmp_path / "_transcript.jsonl")
    pre.append_many([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ])
    # And the session.json has the same 4 messages (in-context).
    mem.save_session([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ])

    a = Agent(memory=mem)
    a.restore_session()

    # No re-append: still 4 records on disk, message_ids points at all 4
    on_disk = Transcript(tmp_path / "_transcript.jsonl").all()
    assert len(on_disk) == 4
    assert a._message_ids == ["000001", "000002", "000003", "000004"]


# ---- defensive ---------------------------------------------------------


def test_agent_without_memory_has_no_transcript(tmp_path: Path) -> None:
    """Memory-less agents (used by some test paths) must not crash on
    journaling — the helpers no-op when transcript is None."""
    a = Agent(memory=None)
    assert a._transcript is None
    a._journal_append({"role": "user", "content": "no memory"})
    # history still appends — transcript silently skipped
    assert a.history[-1] == {"role": "user", "content": "no memory"}
    assert a._message_ids == []
