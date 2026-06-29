"""Output guard — deterministic checks on a final reply before it reaches the user.

Extracted from core.py (Phase 3). These are the phrase lists, regexes, and pure
predicates the agent's output guard runs to catch the failure modes a weak model
produces: leaked internal paths, fabricated links, "I did X" claims with no
matching tool call, claim/target mismatches, and citation artifacts. Agent.
_output_guard (still in core.py) orchestrates them; this module holds the
primitives, with no Agent dependency.
"""

import re


# Output guard — compiled once at module load.
_GUARD_MEMORY_FILENAME_RE = re.compile(
    r"\b(?:user|feedback|project|reference|skill)_[a-z0-9_]+\.md\b"
)
_GUARD_INTERNAL_PATHS = ("workspace/memory/", "memory/logs/", "memory/_")

# gpt-oss / OpenAI-style inline citation markers (【1†URL】, 【2†source】)
# leak into replies verbatim instead of rendering as links. The †
# separator makes this precise — it won't touch ordinary CJK brackets.
_CITATION_ARTIFACT_RE = re.compile(r"\s*【[^】]*†[^】]*】")


def _strip_citation_artifacts(content: str) -> str:
    if not content or "†" not in content:
        return content
    return _CITATION_ARTIFACT_RE.sub("", content)
_GUARD_ERROR_PREFIXES = ("ERROR:", "ERROR running ")
_GUARD_CONFABULATION_TERMS = ("example.com", "example domain")

# Phrases that indicate the model is claiming to have performed an action.
# When combined with zero tool calls, this is a hallucination.
_GUARD_ACTION_CLAIM_PHRASES = (
    "i've created", "i have created",
    "i've set", "i have set",
    "i've added", "i have added",
    "i've scheduled", "i have scheduled",
    "i've sent", "i have sent",
    "i've saved", "i have saved",
    "i've updated", "i have updated",
    "i've deleted", "i have deleted",
    "i've removed", "i have removed",
    "task has been created", "task was created",
    "reminder has been set", "reminder was set",
    "notification has been", "notification was sent",
    "done! i've", "done. i've",
    "i've filed", "i have filed", "i filed",
    "i've proposed", "i have proposed", "i proposed",
    "proposal has been", "proposal was filed", "filed a proposal",
    "i've submitted", "i have submitted",
)

# Per-tool success-claim phrases. If the named tool was CALLED this turn,
# EVERY call to it failed, and the reply asserts that specific action
# succeeded (without acknowledging the failure), the reply is
# confabulating. This generalises success_claim_all_tools_failed to
# per-tool granularity: a turn where OTHER tools succeeded (read_file,
# web_fetch, …) but the action tool failed is still caught. Live case:
# propose_skill returned {"ok": false} yet the agent said "the edit is
# now submitted … awaits your approval" — read_file had succeeded, so the
# all-tools-failed rule (and the generic phrase list, which only matches
# "I've submitted") both missed it.
_GUARD_TOOL_SUCCESS_PHRASES = {
    "propose_skill": (
        "awaits your approval", "awaits approval", "for your approval",
        "for my approval", "filed for approval", "proposal has been",
        "proposal is now", "submitted as a", "edit is now submitted",
        "modification proposal", "filed a proposal", "filed the proposal",
        "submitted the proposal", "proposal is awaiting",
    ),
    "create_task": (
        "task has been created", "task was created", "task is scheduled",
        "reminder has been set", "reminder is set", "i've scheduled",
    ),
    "schedule_task": ("task is scheduled", "rescheduled it", "scheduled it for"),
    "notify": ("notification has been sent", "notification was sent", "i've notified you"),
}

# Terms that mean the reply DOES own up to a failure — when present we do
# not flag a per-tool success claim, so honest "I tried to file it but it
# was rejected: …" replies are never treated as confabulation.
_GUARD_FAILURE_ACK_TERMS = (
    "fail", "error", "could not", "couldn't", "cannot", "can't",
    "reject", "invalid", "blocked", "unable", "wasn't able", "was not able",
    "didn't go through", "did not go through", "went wrong", "n't work",
)

# Artifact-claim verification — the robust replacement for phrase-matching.
# A reply that points the user at a concrete artifact it supposedly created
# (a prop-NNNN skill proposal) is verified against REALITY (the proposal
# store), not against a hand-maintained phrase list. The agent invented
# "prop-0005" and told the user to "approve it on the Overview page" with
# zero tool calls — every phrase-list guard missed it, but a fabricated ID
# cannot survive a check against the store.
_GUARD_PROPOSAL_ID_RE = re.compile(r"\bprop-\d{3,}\b", re.IGNORECASE)

# Completion claims that assert a proposal WAS filed (not explanatory "you
# can approve proposals on the Overview page"). If one of these appears and
# propose_skill did not succeed this turn, the filing is fabricated.
_GUARD_PROPOSAL_FILED_PHRASES = (
    "filed for approval", "awaits your approval", "awaiting your approval",
    "filed a proposal", "filed the proposal", "submitted the proposal",
    "proposal has been filed", "proposal has been submitted",
    "i've filed the proposal", "i have filed the proposal",
    "is now submitted as", "edit is now submitted",
)


def _existing_proposal_ids() -> set[str] | None:
    """Lowercased IDs of every proposal that actually exists in the store
    (any status). Returns None if the store can't be read — the output
    guard must FAIL OPEN (never block a reply because a side file was
    unreadable)."""
    try:
        from homunculus.proposals import _store
        return {str(p.get("id", "")).lower() for p in _store().list("all")}
    except Exception:
        return None



# Phrases that signal an imminent next action ("I will try X next").
# When these appear in a FINAL reply, the loop has already ended — the
# promised action will never happen. This is the same lie shape as
# "action_claim_without_tool_call" but for future intent rather than past
# action. Tight regex on purpose: only catches the construction where a
# first-person promise is followed shortly by an action verb + scope,
# so legitimate uses like "I will help if you tell me X" don't match.
_GUARD_FUTURE_PROMISE_RE = re.compile(
    r"(?i)\b(?:I\s+will|I'll|Let\s+me|Next\s+(?:I\s+(?:will|'ll)|I'll))"
    r"\s+(?:try|attempt|check|read|fetch|search|look|find|see|continue)"
    r"\s+(?:another|the\s+next|more|other|again|now|next)"
)

# Forward-tense MUTATION promise: "I'll edit the skill", "I'll create a task".
# Same lie family as action_claim_without_tool_call (past tense) and
# false_future_promise (retry shape) — the model says it WILL change a concrete
# artifact but the turn ends without calling the tool, so nothing happens. Tight
# on purpose: needs a promise verb AND a concrete artifact noun in the same
# clause ([^.?!] stops at sentence/question boundaries), so "I'll update you"
# (no artifact) and clarifying questions don't match.
_GUARD_MUTATION_PROMISE_RE = re.compile(
    r"(?i)\b(?:I'?ll|I\s+will|let\s+me|I'?m\s+going\s+to)\s+"
    r"(?:edit|update|modify|change|adjust|add|create|write|set\s+up|configure|"
    r"file|propose|register|schedule|build|implement|revise|tweak)\b"
    r"[^.?!]*?\b"
    r"(?:skill|task|brief|reminder|proposal|memory|note|playbook|workflow|"
    r"schedule|quiz|feed)\b"
)

# Tools that actually change persistent state. A mutation PROMISE is only a lie
# if NONE of these ran this turn — if the agent called one, it acted on the
# promise (success/failure of that call is covered by the success-claim guards).
_MUTATING_TOOLS = frozenset({
    "propose_skill", "create_task", "update_task", "delete_task", "cancel_task",
    "complete_task", "record_failure", "write_file", "append_file", "remember",
    "schedule_next_tick", "update_world_state", "quiz_pick",
})


# Map tool names → which arg holds the targeted resource (file path or
# URL). The claim/result consistency check uses this to recover the
# "target" of a tool call, so it can match a path mentioned in the reply
# against the tool calls that touched it.
_CLAIM_TARGET_TOOLS: dict[str, str] = {
    "read_file": "path",
    "write_file": "path",
    "append_file": "path",
    "web_fetch": "url",
}

# Phrases preceding a positive-action claim about a specific target.
# Tuned for false-negative bias: if a reply uses any of these followed
# by a path/URL, we look up whether the matching tool call actually
# succeeded.
_CLAIM_VERBS_RE = re.compile(
    r"(?i)\b(?:i\s+(?:successfully\s+)?(?:read|found|fetched|wrote|"
    r"saved|opened|loaded|retrieved|got)|(?:successfully|just)\s+"
    r"(?:read|fetched|wrote|saved|loaded|retrieved))\b"
)

# Match a path or URL in the reply. Allows /etc/foo, /tmp/bar.yaml, and
# https://example.com/x. Conservative — only catches absolute paths and
# fully-qualified URLs, since relative names like "config" are too noisy.
_CLAIM_TARGET_RE = re.compile(
    r"(?:`|'|\"|^|\s)"
    r"(/[A-Za-z0-9_.\-/]+|https?://[^\s'\"`]+)"
    r"(?:`|'|\"|\.|\s|$)"
)


def _claim_target_inconsistencies(reply: str, tool_outcomes: list[dict]) -> list[str]:
    """Find paths/URLs the reply claims to have acted on successfully,
    where every matching tool call this turn failed.

    Returns the list of (target) strings that triggered. Empty list = no
    inconsistency. The check is intentionally conservative: it only
    fires when (a) a claim verb appears in the reply, (b) followed by an
    absolute path or fully-qualified URL within a few words, and (c)
    EVERY tool call against that target in this turn returned an error.
    """
    # Quick exit: no claim verbs → nothing to check.
    if not _CLAIM_VERBS_RE.search(reply):
        return []

    # Build a map: target → list of success-bools across this turn's calls.
    target_outcomes: dict[str, list[bool]] = {}
    for outcome in tool_outcomes:
        arg_name = _CLAIM_TARGET_TOOLS.get(outcome.get("name", ""))
        if not arg_name:
            continue
        target = (outcome.get("args") or {}).get(arg_name)
        if not isinstance(target, str) or not target.strip():
            continue
        target_outcomes.setdefault(target, []).append(bool(outcome.get("success")))

    if not target_outcomes:
        return []

    # For each claim verb occurrence, scan forward up to ~120 chars for
    # a target and check whether any tool call against it succeeded.
    inconsistent: list[str] = []
    for verb_match in _CLAIM_VERBS_RE.finditer(reply):
        window = reply[verb_match.end(): verb_match.end() + 120]
        for tgt_match in _CLAIM_TARGET_RE.finditer(window):
            target = tgt_match.group(1)
            outcomes = target_outcomes.get(target)
            if outcomes is None:
                continue
            if not any(outcomes):
                inconsistent.append(target)
    return inconsistent


def tool_result_indicates_failure(result) -> bool:
    """Whether a tool result string signals failure.

    Beyond the ERROR/BLOCKED prefixes, recognise STRUCTURED failure: a
    JSON tool result carrying "ok": false (e.g. propose_skill rejecting a
    proposal). Without this, such a result is logged as a success and the
    output guard can't catch a reply that claims it worked — the live
    case where the agent said "I've filed a proposal" after propose_skill
    returned {"ok": false}.
    """
    if not isinstance(result, str):
        return False
    s = result.lstrip()
    if s.startswith(("ERROR", "Error", "BLOCKED:")):
        return True
    compact = s.replace(" ", "")
    return '"ok":false' in compact
