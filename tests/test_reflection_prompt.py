"""Reflection prompt must render without choking on its literal braces.

The reflection template embeds skill-edit examples containing literal JSON
braces — `edits=[{"old": ..., "new": ...}]`. str.format() reads those as
format fields and raises KeyError: '"old"', which silently killed the daily
reflection tick (and with it, skill auto-refinement). The substitution must
use plain replace, leaving literal braces untouched.
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import REFLECTION_PROMPT_TEMPLATE  # noqa: E402


def test_template_has_literal_json_braces():
    """Guard the precondition: the template really does contain literal
    braces that would break str.format()."""
    assert 'edits=[{"old"' in REFLECTION_PROMPT_TEMPLATE
    import pytest
    with pytest.raises(KeyError):
        REFLECTION_PROMPT_TEMPLATE.format(
            today="x", yesterday_path="y", recent_deliveries="z"
        )


def test_replace_substitution_renders_cleanly():
    """The replace-based substitution the tick uses must fill placeholders,
    leave literal braces intact, and never raise."""
    rendered = (
        REFLECTION_PROMPT_TEMPLATE
        .replace("{today}", "2026-06-22")
        .replace("{yesterday_path}", "2026/06/2026-06-21")
        .replace("{recent_deliveries}", "task: brief — delivered")
    )
    # placeholders filled
    assert "{today}" not in rendered
    assert "{yesterday_path}" not in rendered
    assert "{recent_deliveries}" not in rendered
    assert "2026-06-22" in rendered
    # literal skill-edit example preserved verbatim
    assert 'edits=[{"old"' in rendered
