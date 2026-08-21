"""`related:` is written in more shapes than a naive reader expects.

YAML gives a real list for a block sequence, but the agent writes the flow
form — `related: [user_name, favourite_editor]` — and a frontmatter reader
that splits on lines hands that back as ONE string. Taken literally it becomes
a single target named "[user_name, favourite_editor]" which resolves to
nothing: two live links reported as one broken one.

Found by looking at the memory map: an entry whose links I had already
verified resolved was drawing a broken-link stub.
"""

from homunculus.memory import link_slug, parse_related_field


def test_a_real_yaml_list():
    assert parse_related_field(["user_name", "favourite_editor"]) == {
        "user_name", "favourite_editor"
    }


def test_the_flow_form_a_line_parser_hands_back_as_one_string():
    """The bug: this used to become a single bogus target."""
    assert parse_related_field("[user_name, favourite_programming_language]") == {
        "user_name", "favourite_programming_language"
    }


def test_a_bare_single_value():
    assert parse_related_field("user_name") == {"user_name"}


def test_wikilink_brackets_are_stripped():
    assert parse_related_field("[[skill_daily_brief]]") == {"skill_daily_brief"}
    assert parse_related_field(["[[a_one]]", "[[b_two]]"]) == {"a_one", "b_two"}


def test_hyphen_and_underscore_are_one_reference():
    assert parse_related_field("[feedback-tone, feedback_tone]") == {"feedback_tone"}


def test_empty_shapes_are_empty():
    for v in (None, "", [], "[]", "  ", [""]):
        assert parse_related_field(v) == set()


def test_quotes_and_padding_do_not_survive():
    assert parse_related_field('["user_name" , \'user_role\']') == {"user_name", "user_role"}


def test_link_slug_matches_the_reader():
    assert link_slug("Feedback-Tone") == link_slug("feedback_tone") == "feedback_tone"
