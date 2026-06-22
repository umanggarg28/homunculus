"""notify_links_grounded — delivered links must come from a tool result.

The morning brief once delivered five `example.com/news1..5` placeholder
links: the model skipped `news_headlines` and fabricated them, and they
passed `notify_matches: "https?://"` (a shape check satisfied by any URL).

The fix verifies delivery against the actual tool output, not the model's
narration: every URL in the notify text must appear verbatim in something a
tool returned this run. A link no tool produced is fabricated → criterion
fails → the task records a failure instead of shipping a bad brief.
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import TaskGuard, _extract_urls  # noqa: E402

GROUNDED = {"t1": [{"type": "notify_links_grounded"}]}

_REAL = (
    "- [US-Iran talks resume](https://www.bbc.com/news/articles/cwyekkwm1mmo?at_medium=RSS)\n"
    "- [Signal on AI chatbots](https://techcrunch.com/2026/06/20/signal-meredith/)"
)


# ---- _extract_urls --------------------------------------------------------

def test_extract_urls_trims_trailing_punctuation_and_dedupes():
    text = "see (https://x.com/a). also https://x.com/a and https://y.io/b!"
    assert _extract_urls(text) == ["https://x.com/a", "https://y.io/b"]


def test_extract_urls_empty():
    assert _extract_urls("") == []
    assert _extract_urls("no links here") == []


# ---- the criterion --------------------------------------------------------

def test_grounded_links_pass():
    """URLs pasted verbatim from a tool result are allowed through."""
    guard = TaskGuard(GROUNDED)
    guard.observe_tool_result("news_headlines", _REAL)
    blocked = guard.on_tool_call("notify", {"text": "Top headlines:\n" + _REAL})
    assert blocked is None


def test_fabricated_link_is_blocked():
    """The exact failure mode: example.com placeholders, no fetch tool called."""
    guard = TaskGuard(GROUNDED)
    # Agent only called task_health_summary (no URLs), then fabricated links.
    guard.observe_tool_result("task_health_summary", '{"today_commitments": []}')
    text = (
        "Morning, Umang\n\nTop headlines:\n"
        "- https://example.com/news1\n- https://example.com/news2"
    )
    blocked = guard.on_tool_call("notify", {"text": text})
    assert blocked is not None
    assert "no tool returned this run" in blocked
    assert "example.com/news1" in blocked


def test_notify_without_links_passes():
    """A link-free delivery has nothing to ground — criterion is a no-op."""
    guard = TaskGuard(GROUNDED)
    guard.observe_tool_result("task_health_summary", '{"x": 1}')
    assert guard.on_tool_call("notify", {"text": "Morning, Umang. Nothing on your plate."}) is None


def test_notify_own_result_cannot_self_validate():
    """notify's own result is NOT a grounding source — a fabricated link
    echoed only by notify() must still be rejected."""
    guard = TaskGuard(GROUNDED)
    guard.observe_tool_result("notify", "DELIVERED to user: https://example.com/fake")
    blocked = guard.on_tool_call("notify", {"text": "See https://example.com/fake"})
    assert blocked is not None
    assert "example.com/fake" in blocked


def test_partial_fabrication_is_blocked():
    """One real link plus one invented link still fails — the invented one
    is named so the agent can fix exactly that line."""
    guard = TaskGuard(GROUNDED)
    guard.observe_tool_result("news_headlines", _REAL)
    text = _REAL + "\n- https://example.com/made-up"
    blocked = guard.on_tool_call("notify", {"text": text})
    assert blocked is not None
    assert "example.com/made-up" in blocked
    assert "bbc.com" not in blocked   # the real link isn't flagged


def test_grounding_ignores_trailing_markdown_punctuation():
    """A URL the model wrapped in markdown/parens still grounds against the
    bare URL in the tool result."""
    guard = TaskGuard(GROUNDED)
    guard.observe_tool_result("web_fetch", "source: https://example.org/post-42 (fetched)")
    blocked = guard.on_tool_call("notify", {"text": "Read more: (https://example.org/post-42)."})
    assert blocked is None
