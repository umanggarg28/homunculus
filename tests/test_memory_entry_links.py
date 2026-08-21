"""Memory links come from BOTH places the agent writes them.

`[[wikilink]]` is the form used in prose; `related:` frontmatter is the form
`remember(related=[...])` writes. Reading only the first halves the graph the
memory map draws, and the omission is invisible — an entry whose links were
never parsed looks exactly like an entry that has none.
"""

from homunculus.transports.web_api import _entry_links, _slug


def test_body_wikilinks_are_found():
    body = "See [[project_homunculus]] and [[feedback_tone]] for context."
    assert _entry_links(body, {}) == ["feedback_tone", "project_homunculus"]


def test_related_frontmatter_is_found():
    meta = {"related": ["skill_scheduling", "reference_mcp"]}
    assert _entry_links("no links in the body", meta) == ["reference_mcp", "skill_scheduling"]


def test_both_sources_merge_and_dedupe():
    body = "[[skill_scheduling]] matters here."
    meta = {"related": ["skill_scheduling", "user_preferences"]}
    assert _entry_links(body, meta) == ["skill_scheduling", "user_preferences"]


def test_hyphen_and_underscore_resolve_to_one_node():
    """The vault uses both conventions; they must not become two nodes."""
    body = "[[feedback-tone]]"
    meta = {"related": ["feedback_tone"]}
    assert _entry_links(body, meta) == ["feedback_tone"]


def test_related_accepts_a_bare_string():
    assert _entry_links("", {"related": "user_identity"}) == ["user_identity"]


def test_missing_and_empty_related_are_safe():
    assert _entry_links("", {}) == []
    assert _entry_links("", {"related": None}) == []
    assert _entry_links("", {"related": []}) == []
    assert _entry_links("", {"related": ["", "  "]}) == []


def test_slug_is_case_and_separator_insensitive():
    assert _slug("Feedback-Tone") == _slug("feedback_tone") == "feedback_tone"
