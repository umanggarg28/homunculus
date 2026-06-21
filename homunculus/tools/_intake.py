"""Task-intake clarifier — pure helpers, no package-relative imports.

Lives outside scheduling.py so that tests can import these heuristics
directly without dragging in the rest of the scheduling module's
package-relative imports (which fail under the conftest test stubs).
"""

from __future__ import annotations


_TEMPORAL_MARKERS = (
    "am", "pm",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "tomorrow", "tonight", "today", "morning", "evening", "afternoon", "noon",
    "in 1 hour", "in 2 hour", "in 3 hour", "in an hour", "in a few", "in n",
    "every day", "every week", "daily", "weekly", "hourly",
)


def has_temporal_marker(text: str) -> bool:
    """Heuristic: does this title/description contain a time/date signal?

    Used by the intake clarifier to detect vague tasks that arrived without
    an explicit `due_at`. If the title says "remind me at 9am" we infer
    a temporal intent even without due_at; if it says "remind me about
    jobs" we want to clarify before saving.
    """
    haystack = text.lower()
    return any(marker in haystack for marker in _TEMPORAL_MARKERS)


def needs_clarification(
    title: str,
    description: str,
    due_at: str | None,
    recurrence: str,
) -> str | None:
    """Return a clarification request if the task is too vague to act on.

    Item 7 of the robustness plan: under-specified tasks silently fail at
    the first heartbeat run because the agent can't figure out what the
    user wanted. Better to surface the ambiguity at intake time.

    A task is "vague" when ALL of:
      - No due_at provided
      - Recurrence is "none" (recurring tasks have implicit cadence)
      - Title + description contain no temporal marker
    """
    if due_at:
        return None  # explicit time given — not vague
    if recurrence and recurrence != "none":
        return None  # cadence supplied implicitly
    combined = f"{title} {description}"
    if has_temporal_marker(combined):
        return None  # natural-language time present in the prose
    return (
        f"NEEDS_CLARIFICATION: the task '{title}' has no due_at, no "
        f"recurrence, and no time mentioned in the title or description. "
        f"Ask the user when this should fire — e.g. 'tomorrow 9am', 'in 2 hours', "
        f"or 'daily at 6pm' — before saving. If the user already specified a "
        f"time and you missed it, re-read their message."
    )
