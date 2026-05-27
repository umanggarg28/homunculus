"""
End-to-end action tests for the running Homunculus web agent.

Tests what the agent DOES (file ops, code exec, task management,
multi-step workflows) — not just what it knows.

Prerequisites:
  docker compose up -d web    (web must be running on port 8765)

Run:
  uv run pytest tests/test_agent_actions.py -v -s

Each test sends one message via the SSE chat endpoint and asserts on
the reply. Tests run sequentially — earlier file writes are visible to
later read/search tests.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("HOMUNCULUS_URL", "http://localhost:8765")
TIMEOUT = int(os.environ.get("AGENT_TEST_TIMEOUT", "120"))

# ── helpers ──────────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    env = Path(__file__).parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("HOMUNCULUS_WEB_PASSWORD="):
                pwd = line.split("=", 1)[1].strip()
                if pwd:
                    return {"Authorization": f"Bearer {pwd}"}
    return {}


def chat(message: str, timeout: int = TIMEOUT) -> str:
    """Send a message and collect the full SSE reply. Blocks until done."""
    chunks: list[str] = []
    with httpx.stream(
        "POST",
        f"{BASE_URL}/api/chat/send",
        json={"message": message},
        headers=_auth_headers(),
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data == "end":
                    break
                if data:
                    chunks.append(data)
    time.sleep(1)  # let the agent settle before the next turn
    return "".join(chunks)


def contains_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def contains_all(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return all(k.lower() in low for k in keywords)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def check_server():
    """Skip entire session if the web server isn't reachable."""
    try:
        httpx.get(f"{BASE_URL}/api/mode", timeout=5).raise_for_status()
    except Exception as e:
        pytest.skip(f"Homunculus web not reachable at {BASE_URL}: {e}")


# ── 1. File discovery ────────────────────────────────────────────────────────

class TestFileDiscovery:
    def test_list_workspace_root(self):
        r = chat("Use list_files to list the files in my workspace root. What folders are there?")
        assert len(r) > 5
        assert contains_any(r, ["memory", "tasks", "scripts", "notes", "logs"]), \
            f"Expected workspace folders in reply, got: {r[:200]}"

    def test_list_notes_folder(self):
        # notes/ may not exist yet — agent should say so gracefully
        r = chat("Use list_files on the notes/ folder. What's in it?")
        assert len(r) > 2
        assert not contains_any(r, ["Traceback", "traceback"]), \
            f"Unexpected error trace: {r[:200]}"


# ── 2. File write + read ─────────────────────────────────────────────────────

class TestFileWriteRead:
    def test_write_file(self):
        r = chat(
            "Create notes/standup.md with exactly this content:\n"
            "# Daily Standup\n"
            "- Yesterday: fixed the timezone bug\n"
            "- Today: writing action tests\n"
            "- Blockers: none"
        )
        assert contains_any(r, ["wrote", "created", "saved", "done"]), \
            f"Expected confirmation of write, got: {r[:200]}"

    def test_read_file(self):
        r = chat("Read back notes/standup.md")
        assert contains_all(r, ["timezone", "Blockers"]), \
            f"File content not reflected: {r[:200]}"

    def test_append_file(self):
        r = chat("Append this line to notes/standup.md: '- Note: 21/21 tests passing'")
        assert contains_any(r, ["appended", "added", "wrote", "done"]), \
            f"Expected append confirmation: {r[:200]}"

    def test_append_preserved_original(self):
        r = chat("Read notes/standup.md — does it still have 'Yesterday' AND '21/21'?")
        assert "yesterday" in r.lower(), f"Original content lost after append: {r[:200]}"
        assert contains_any(r, ["21/21", "21", "passing"]), \
            f"Appended content missing: {r[:200]}"


# ── 3. File search ───────────────────────────────────────────────────────────

class TestFileSearch:
    def test_search_known_term(self):
        r = chat("Search my workspace files for the word 'timezone' — which files mention it?")
        assert contains_any(r, ["standup", ".md", "memory", "notes", "heartbeat"]), \
            f"Expected file reference in search result: {r[:200]}"

    def test_search_leetcode(self):
        r = chat("Search my workspace for 'LeetCode' and show the files and line numbers.")
        assert contains_any(r, ["memory", "tasks", ".md", ".json", "leetcode"]), \
            f"Expected LeetCode references: {r[:200]}"

    def test_search_no_match(self):
        r = chat("Search my workspace for 'xyzzy_nonexistent_token_abc123'")
        assert contains_any(r, ["no match", "not found", "nothing", "no result", "0"]), \
            f"Expected no-match message: {r[:200]}"


# ── 4. Code execution ────────────────────────────────────────────────────────

class TestCodeExecution:
    def test_python_computation(self):
        r = chat("Use Python to compute 2 raised to the power 32.")
        assert "4294967296" in r, f"Wrong or missing result: {r[:200]}"

    def test_python_fibonacci(self):
        r = chat("Use Python to print the first 10 Fibonacci numbers.")
        # 10th Fibonacci = 34 (0-indexed: 0,1,1,2,3,5,8,13,21,34)
        assert contains_any(r, ["34", "55"]), f"Fibonacci missing: {r[:200]}"

    def test_python_password(self):
        r = chat(
            "Use Python to generate a random 16-character password "
            "with uppercase, lowercase, digits, and symbols. Show me the result."
        )
        assert len(r) > 5, f"Too short: {r!r}"
        assert not contains_any(r, ["error", "Error"]) or contains_any(r, ["password", "generated"]), \
            f"Unexpected error: {r[:200]}"

    def test_write_and_run_script(self):
        r = chat(
            "Write a Python script to scripts/is_prime.py that checks if a number is prime, "
            "then use it to check whether 997 is prime."
        )
        assert contains_any(r, ["prime", "997", "yes"]), \
            f"Expected prime result: {r[:200]}"


# ── 5. Task management ───────────────────────────────────────────────────────

class TestTaskManagement:
    def test_create_recurring_task(self):
        r = chat(
            "Create a recurring task: every Sunday at 9 AM IST, "
            "remind me to back up my code. Title: 'Weekly code backup'"
        )
        assert contains_any(r, ["created", "scheduled", "task", "sunday", "backup"]), \
            f"Expected task creation confirmation: {r[:200]}"

    def test_list_tasks(self):
        r = chat("List all my active tasks.")
        assert contains_any(r, ["LeetCode", "backup", "quarterly", "PR"]), \
            f"Expected task list: {r[:200]}"

    def test_cancel_task(self):
        r = chat("Cancel the 'Weekly code backup' task I just created.")
        assert contains_any(r, ["cancelled", "deleted", "removed", "done", "cancel"]), \
            f"Expected cancellation confirmation: {r[:200]}"

    def test_one_shot_reminder(self):
        r = chat("Schedule a one-time reminder for tomorrow at 10 AM IST: 'Review open PRs'")
        assert contains_any(r, ["scheduled", "created", "reminder", "task", "done"]), \
            f"Expected reminder creation: {r[:200]}"


# ── 6. Memory ────────────────────────────────────────────────────────────────

class TestMemory:
    def test_save_fact(self):
        r = chat("Remember: my primary machine is a MacBook Pro M3 Max with 36GB RAM.")
        assert contains_any(r, ["saved", "remembered", "noted", "memory", "stored"]), \
            f"Expected memory save confirmation: {r[:200]}"

    def test_recall_fact(self):
        r = chat("What computer do I use? Check your memory.")
        assert contains_any(r, ["MacBook", "M3", "36GB", "RAM"]), \
            f"Expected hardware recall: {r[:200]}"

    def test_recall_earlier_session(self):
        r = chat("Do you remember the Phoenix project I mentioned earlier this session?")
        assert "phoenix" in r.lower(), f"Phoenix not recalled: {r[:200]}"

    def test_identity(self):
        r = chat("What is my name?")
        assert "umang" in r.lower(), f"Name not recalled: {r[:200]}"
        assert "alex" not in r.lower(), f"Wrong name (Alex) returned: {r[:200]}"


# ── 7. Multi-step workflows ──────────────────────────────────────────────────

class TestMultiStep:
    def test_web_search_then_write(self):
        r = chat(
            "Search the web for 'Python 3.13 key new features', "
            "write a 3-bullet summary to notes/python313.md, "
            "and confirm when done.",
            timeout=150,
        )
        assert contains_any(r, ["wrote", "saved", "python313", "notes", "done"]), \
            f"Expected write confirmation: {r[:200]}"

    def test_read_written_content(self):
        r = chat("Read notes/python313.md and summarise the first bullet.")
        assert contains_any(r, ["python", "3.13", "feature", "free-threaded", "JIT", "type", "GIL"]), \
            f"Expected Python 3.13 content: {r[:200]}"

    def test_compute_then_write(self):
        r = chat(
            "Using Python, calculate compound interest: "
            "principal=$1500, rate=10%, years=20, compounded annually. "
            "Write the result to notes/investment.md and tell me the final amount."
        )
        # $1500 * (1.1^20) ≈ $10,091
        assert contains_any(r, ["10,", "9,", "1500", "investment", "wrote", "saved"]), \
            f"Expected compound interest result: {r[:200]}"

    def test_notes_folder_listing(self):
        r = chat("List everything in my notes/ folder now.")
        assert contains_any(r, ["standup", "python313", "investment"]), \
            f"Expected all created notes: {r[:200]}"


# ── 8. Robustness ────────────────────────────────────────────────────────────

class TestRobustness:
    def test_file_not_found(self):
        r = chat("Read notes/definitely_does_not_exist_xyzzy.md")
        assert contains_any(r, ["not found", "doesn't exist", "error", "no such", "cannot"]), \
            f"Expected graceful error: {r[:200]}"
        assert not contains_any(r, ["Traceback"]), f"Raw traceback leaked: {r[:200]}"

    def test_knows_new_tools(self):
        r = chat("List all the tools you have available.")
        assert contains_any(r, ["list_files", "append_file", "search_files"]), \
            f"New tools not in tool list: {r[:200]}"

    def test_no_identity_hallucination(self):
        r = chat("Am I Alex or Umang?")
        assert "umang" in r.lower(), f"Wrong identity: {r[:200]}"
        assert "alex" not in r.lower(), f"Leaked stress-test identity: {r[:200]}"

    def test_timezone_batch_efficiency(self):
        """One get_current_time call should handle all 3 cities."""
        r = chat("What time is it in Tokyo, London, and New York?")
        assert contains_all(r, ["JST", "BST", "EDT"]) or \
               contains_all(r, ["Tokyo", "London", "New York"]), \
            f"Expected all 3 cities: {r[:200]}"
