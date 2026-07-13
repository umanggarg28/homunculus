"""Unclosed-code-fence gate on notify — the truncated-delivery defense.

Live failure 2026-07-09: the primary model was cooled, the run fell back
to gemini-2.5-flash, and the notify tool-call came back as VALID JSON
whose text simply stopped mid-solution (`line = `) — a half-finished
LeetCode answer reached the user. No criterion caught it: notify_called
passed, min_chars passed, and notify_has_code only requires one ``` to
be present.

Fences always pair, so an odd fence count is deterministic proof the
message was cut mid-generation. The TaskGuard refuses the send and the
model regenerates — same shape as the sentinel gate beside it.
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import TaskGuard  # noqa: E402


TRUNCATED = (
    "**LeetCode Daily - 68. Text Justification**\n"
    "**Solution (Python):**\n"
    "```python\n"
    "class Solution:\n"
    "    def fullJustify(self, words, maxWidth):\n"
    "        line = "
)

COMPLETE = (
    "**LeetCode Daily - Valid Palindrome**\n"
    "```python\n"
    "def isPalindrome(s): ...\n"
    "```\n"
    "**Complexity:** O(n).\n"
    "```python\n"
    "print(isPalindrome('aba'))\n"
    "```"
)


def test_unclosed_fence_is_blocked():
    guard = TaskGuard({"leetcode": [{"type": "notify_called"}]})
    out = guard.on_tool_call("notify", {"text": TRUNCATED})
    assert out is not None and "BLOCKED" in out
    assert "unclosed" in out.lower()
    assert guard._notify_texts == []  # nothing recorded as delivered


def test_paired_fences_pass():
    guard = TaskGuard({"leetcode": [{"type": "notify_called"}]})
    assert guard.on_tool_call("notify", {"text": COMPLETE}) is None
    assert guard._notify_texts == [COMPLETE]


def test_plain_text_without_fences_passes():
    guard = TaskGuard({"reminder": [{"type": "notify_called"}]})
    assert guard.on_tool_call("notify", {"text": "Preheat the oven"}) is None
