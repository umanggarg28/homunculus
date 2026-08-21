"""The one definition of a tool-failure sentinel.

A data-source tool that cannot reach its source returns an uppercase token
instead of raising, so the MODEL knows to omit that section rather than
invent one. Those tokens travel three channels, and every channel has to
agree on the same set:

- ``output_guard`` treats a result opening with one as a FAILED tool call,
  which is what keeps a reply from claiming work an outage prevented;
- ``task_guard`` refuses to deliver one to the user, because a sentinel in a
  notify body is either a verbatim paste or a fabricated failure branch;
- ``TaskGuard.every_required_source_failed`` blocks ``complete_task`` when a
  skill's declared sources all failed.

They disagreed before this module existed. Each tool invented its own
spelling, and two shapes emerged -- ``GMAIL_UNAVAILABLE`` but ``WEATHER
UNAVAILABLE`` -- while the recognizers were written against the underscore
shape alone. A tool whose sentinel is not recognised has no failure the
harness can see, so its outage reads as a clean run all the way through.

The registry below is therefore the source of truth, and ``tests/
test_sentinel_registry.py`` asserts that every ``*UNAVAILABLE`` literal in
``homunculus/tools/`` appears here. Adding a sentinel without registering it
fails CI, which is the property that keeps the three channels in agreement.

The tokens carry their historical wire text on purpose. They appear in
model-facing tool descriptions and in approved skill files under
``workspace/memory/``, and a skill is only ever edited through the
``propose_skill`` approval gate -- so normalising the spelling here would
mean rewriting approved skills behind that gate. The registry unifies the
CODE without touching the contract those skills were approved against.
"""

from __future__ import annotations

import re

CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"
GMAIL_UNAVAILABLE = "GMAIL_UNAVAILABLE"
LEETCODE_NEXT_UNAVAILABLE = "LEETCODE_NEXT_UNAVAILABLE"
NEWS_UNAVAILABLE = "NEWS_UNAVAILABLE"
# Space-separated, and load-bearing: see the module docstring.
CAREER_CONTEXT_UNAVAILABLE = "CAREER CONTEXT UNAVAILABLE"
POSTING_UNAVAILABLE = "POSTING UNAVAILABLE"
WEATHER_UNAVAILABLE = "WEATHER UNAVAILABLE"

#: Every sentinel any tool may return. Longest first so that alternation
#: prefers the most specific token (``CAREER CONTEXT UNAVAILABLE`` must not
#: be shadowed by a shorter prefix match).
SENTINELS: tuple[str, ...] = tuple(
    sorted(
        (
            CALENDAR_UNAVAILABLE,
            GMAIL_UNAVAILABLE,
            LEETCODE_NEXT_UNAVAILABLE,
            NEWS_UNAVAILABLE,
            CAREER_CONTEXT_UNAVAILABLE,
            POSTING_UNAVAILABLE,
            WEATHER_UNAVAILABLE,
        ),
        key=len,
        reverse=True,
    )
)

#: Anchored to the start of a result on purpose. A tool that merely QUOTES a
#: sentinel is reporting content, not failing: read_file returning a log line
#: that mentions an outage, or recall returning a memory entry about one.
#: Matching those as failures would make the harness distrust real data.
_ANCHORED_RE = re.compile("|".join(re.escape(s) for s in SENTINELS))


def starts_with_sentinel(text: str) -> bool:
    """Whether a tool result OPENS with a sentinel, i.e. the tool failed."""
    return bool(_ANCHORED_RE.match(text.lstrip()))


def find_sentinel(text: str) -> str | None:
    """The first sentinel appearing anywhere in `text`, or None.

    Unanchored, unlike `starts_with_sentinel`: this answers "is a machine
    token leaking into user-facing prose", where position carries no meaning
    and a sentinel buried mid-paragraph is exactly the case to catch.
    """
    m = _ANCHORED_RE.search(text)
    return m.group(0) if m else None
