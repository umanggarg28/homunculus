"""Output guard — deterministic checks on a final reply before it reaches the user.

The complete guard for the failure modes a weak model produces: leaked
internal paths, fabricated links, "I did X" claims with no matching tool
call, claim/target mismatches, and citation artifacts. The phrase lists,
regexes, and pure predicates come first; `run_output_guard` orchestrates
them and `correction_prompt_for` picks the retry instruction for whatever
it caught. Agent delegates here (`Agent._output_guard` / `_self_correct`);
nothing in this module depends on Agent.
"""

import logging
import re

from homunculus import events

log = logging.getLogger(__name__)


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


# Ungrounded-URL guard. A reply that cites web links the model did NOT obtain
# from a tool this turn is fabricating sources (baseline probe #1: ran
# web_search yet cited invented URLs like star-history.com/langchain-ai/open-swe).
# Gated to turns where a web tool actually ran, so pure-knowledge Q&A — where the
# model may legitimately cite a URL it knows — is never touched. Mirrors the
# heartbeat delivery gate's "must appear verbatim in a tool result" rule.
_WEB_GROUNDING_TOOLS = frozenset({"web_search", "web_fetch", "news_headlines", "rss_feed"})
_URL_IN_REPLY_RE = re.compile(r"https?://[^\s<>()\[\]'\"`]+")


def _normalize_url(u: str) -> str:
    return u.rstrip("/.,;:)]}>\"'`").lower()


def ungrounded_urls(reply: str, tool_outcomes: list[dict], tool_names_used) -> list[str]:
    """Reply URLs absent from this turn's successful tool results.

    Returns [] when no web tool ran this turn (the gate is research-scoped:
    only when the model fetched/searched must its links come from results).
    A cited URL is grounded if it appears verbatim in any successful tool
    result text or was the URL argument of a successful web tool call.
    """
    if not (set(tool_names_used) & _WEB_GROUNDING_TOOLS):
        return []
    cited = _URL_IN_REPLY_RE.findall(reply)
    if not cited:
        return []

    grounded_parts: list[str] = []
    for o in tool_outcomes:
        if not o.get("success"):
            continue
        grounded_parts.append(str(o.get("result", "")))
        args = o.get("args")
        if isinstance(args, dict):
            grounded_parts.extend(
                v for v in args.values() if isinstance(v, str) and v.startswith("http")
            )
    grounded_blob = "\n".join(grounded_parts).lower()

    return [u for u in cited if _normalize_url(u) not in grounded_blob]


# Unsupported-cadence guard. The task system's recurrence vocabulary is exactly
# {none, daily, weekly} (tasks.ALLOWED_RECURRENCE). A reply that promises a
# cadence outside that — after a scheduling tool ran — is over-claiming a
# capability the tool doesn't have (baseline probe #9: "every weekday … skip
# public holidays"). The term list enumerates the *finite* gap of a 3-value
# vocabulary, so it's capability gating, not open-ended phrase whack-a-mole.
_SCHEDULING_TOOLS = frozenset({"create_task", "schedule_task"})
_UNSUPPORTED_CADENCE_TERMS = (
    "weekday", "week day", "monday through friday", "mon-fri", "mon through fri",
    "monday to friday", "weekend", "holiday", "every other", "alternate day",
    "alternating", "bi-weekly", "biweekly", "fortnight", "monthly", "every month",
)
# Markers that the reply OWNS the limitation — then it's honest, not an
# over-claim, so we don't flag it.
_CADENCE_LIMITATION_TERMS = (
    "can't", "cannot", "can not", "not supported", "doesn't support",
    "does not support", "unable", "won't be able", "isn't supported",
    "not able to", "you'll need to", "you will need to", "manually",
)


def unsupported_cadence_claim(reply: str, tool_names_used) -> bool:
    """A scheduling tool ran and the reply promises a cadence the recurrence
    vocabulary (none/daily/weekly) cannot express, without owning the limit."""
    if not (set(tool_names_used) & _SCHEDULING_TOOLS):
        return False
    low = reply.lower()
    if not any(t in low for t in _UNSUPPORTED_CADENCE_TERMS):
        return False
    return not any(t in low for t in _CADENCE_LIMITATION_TERMS)
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


# --- Orchestration -----------------------------------------------------------
# run_output_guard is the full validation pass Agent runs on a final reply;
# correction_prompt_for picks the retry instruction for whatever it caught.
# They live here with the primitives so the whole guard — checks, phrase
# tables, and the coaching that fixes a failure — reads as one unit.


def run_output_guard(
    reply: str,
    tool_names_used: set,
    tool_outcomes: list[dict] | None = None,
    *,
    tools_available: bool = True,
) -> tuple[str | None, list[str]]:
    """Validate a final reply before it reaches the user.

    Catches deterministic failure modes:
      1. Memory filename leak — internal *.md paths in reply
      2. Internal path leak — workspace/memory/… strings in reply
      3. Error echo — LLM forwarded a tool ERROR string verbatim
      4. Example.com confabulation — placeholder site cited with no
         web tool active this turn
      5. Claim/result inconsistency — the reply claims to have read,
         fetched, written, or saved a specific path/URL, but the
         matching tool call in this turn returned an error. Catches
         hallucinated tool success (stress probe #22).

    `tools_available` gates the checks that only make sense when the model
    COULD have called a tool (action claims, mutation promises) — a
    Q&A-only session with no registered tools is never flagged for them.

    Returns (reply, []) if clean, or (None, violations) if not.
    None signals the caller to attempt self-correction before
    falling back to a static error message.
    """
    violations: list[str] = []
    tool_outcomes = tool_outcomes or []

    # Strip leaked citation markers (【1†url】) so they never reach the user
    # and every check below runs on the cleaned text. The per-iteration strip
    # in the loop only cleans the journaled copy, not this returned reply.
    reply = _strip_citation_artifacts(reply)

    # File-path guards are intentionally bypassed when the agent explicitly
    # searched or listed files — those results belong in the reply.
    file_search_active = bool(tool_names_used & {"search_files", "list_files"})

    if not file_search_active and _GUARD_MEMORY_FILENAME_RE.search(reply):
        violations.append("memory_filename_leak")

    if not file_search_active and any(p in reply for p in _GUARD_INTERNAL_PATHS):
        violations.append("internal_path_leak")

    if reply.lstrip().startswith("ERROR:") or "ERROR running " in reply:
        violations.append("error_echo")

    # Raw tool-call syntax in a final reply: the model wrote its harmony
    # channel markup as TEXT instead of emitting a real tool call — the
    # call never executed, and the prose around it typically claims the
    # work happened (observed live: "<|start|>assistant<|channel|>
    # commentary to=functions.job_posting …" followed by "the complete
    # application is now saved" with zero calls made).
    if any(m in reply for m in ("<|start|>", "<|channel|>", "<|message|>", "to=functions.")):
        violations.append("tool_syntax_leak")

    # "Everything is drafted/saved" while this turn's own tool results
    # still listed open questions. The weak model quits a long drafting
    # sequence after a few calls and narrates completion (observed live:
    # 2 of 12 questions drafted, reply: "All required fields have been
    # drafted and saved").
    _drafting_open = any(
        "Still needing answers" in str(o.get("result") or "")
        for o in (tool_outcomes or [])
        if o.get("name") == "draft_answer"
    ) and not any(
        "all free-text questions answered" in str(o.get("result") or "").lower()
        for o in (tool_outcomes or [])
    )
    if _drafting_open and _CLAIMS_DRAFTING_DONE_RE.search(reply):
        violations.append("drafting_completion_claim")

    lower_reply = reply.lower().replace("\n", " ")
    if any(t in lower_reply for t in _GUARD_CONFABULATION_TERMS):
        if not (tool_names_used & {"web_fetch", "web_search"}):
            violations.append("example_com_confabulation")

    # Catch hallucinated actions: model claims to have done something
    # (created a task, sent a notification, etc.) without calling any
    # tools. Only fires when tools are registered (not a Q&A-only session).
    claims_action = any(phrase in lower_reply for phrase in _GUARD_ACTION_CLAIM_PHRASES)
    if not tool_names_used and tools_available:
        if claims_action:
            violations.append("action_claim_without_tool_call")

    # Confabulated success: the reply claims an action succeeded, but
    # tools WERE called this turn and EVERY one failed. The live case:
    # propose_skill returned {"ok": false} (validation rejected it) and
    # the agent replied "I've filed a proposal." The all-failed
    # condition keeps this conservative — a turn that also had a
    # successful tool call won't trip (the claim may be about that).
    if claims_action and tool_outcomes and not any(o.get("success") for o in tool_outcomes):
        violations.append("success_claim_all_tools_failed")

    # Per-tool confabulation: the specific action tool was called this
    # turn, EVERY call to it failed, and the reply asserts that action
    # succeeded without owning the failure. Fires even when other tools
    # succeeded (the all-tools-failed rule above does not). Conservative:
    # needs the tool actually called + all its calls failed + a matching
    # success phrase + no failure acknowledgement anywhere in the reply.
    if tool_outcomes and "success_claim_all_tools_failed" not in violations:
        acknowledges_failure = any(t in lower_reply for t in _GUARD_FAILURE_ACK_TERMS)
        if not acknowledges_failure:
            for tool_name, phrases in _GUARD_TOOL_SUCCESS_PHRASES.items():
                calls = [o for o in tool_outcomes if o.get("name") == tool_name]
                if calls and not any(o.get("success") for o in calls):
                    if any(p in lower_reply for p in phrases):
                        violations.append("success_claim_tool_failed")
                        break

    # Artifact-claim verification (the robust, phrasing-independent rule).
    # propose_skill succeeded this turn → any prop-NNNN it minted is now
    # in the store, so legit filings pass.
    proposed_ok = any(
        o.get("name") == "propose_skill" and o.get("success") for o in tool_outcomes
    )
    # (a) The reply cites a concrete proposal ID — verify it actually
    #     exists. A fabricated "prop-0005" fails against the store. This
    #     is what caught the live confabulation that every phrase guard
    #     missed (the agent invented the ID with zero tool calls).
    cited_ids = {m.lower() for m in _GUARD_PROPOSAL_ID_RE.findall(reply)}
    if cited_ids:
        real_ids = _existing_proposal_ids()
        if real_ids is not None and not cited_ids.issubset(real_ids):
            violations.append("unverified_artifact_claim")
    # (b) No ID, but the reply asserts a COMPLETED filing while
    #     propose_skill did not succeed this turn → fabricated.
    if (
        "unverified_artifact_claim" not in violations
        and not proposed_ok
        and any(p in lower_reply for p in _GUARD_PROPOSAL_FILED_PHRASES)
    ):
        violations.append("unverified_artifact_claim")

    # Claim/result inconsistency — the reply asserts a successful action
    # against a specific target (file path or URL) but the matching tool
    # call in this turn errored. Stress probe #22: agent called
    # read_file three times, all returned ENOENT, then replied "I found
    # and read /etc/secret_config.yaml". The check is conservative —
    # only fires when (a) a target is explicitly mentioned and (b) ALL
    # tool calls in this turn against that target failed.
    if tool_outcomes:
        inconsistencies = _claim_target_inconsistencies(reply, tool_outcomes)
        if inconsistencies:
            violations.append("claim_inconsistent_with_tool_result")

    # False future-promise: the reply ends the turn but commits to
    # an imminent next action ("I will try another filename"). The
    # loop has terminated; that promised action will never run. Same
    # lie shape as action_claim_without_tool_call but for the future
    # rather than the past. Stress probe: agent tried 3 paths,
    # failed all, then said "I will try another configuration
    # filename" with no further tool call.
    if _GUARD_FUTURE_PROMISE_RE.search(reply):
        violations.append("false_future_promise")

    # Ungrounded URLs — only when a web tool ran this turn. A research reply
    # must cite links from its own results, never invented ones (baseline
    # probe #1). Pure-knowledge Q&A is not gated.
    if ungrounded_urls(reply, tool_outcomes, tool_names_used):
        violations.append("ungrounded_url")

    # Unsupported cadence — a scheduling tool ran but the reply promises a
    # recurrence the system can't express (weekday-only, skip-holidays,
    # monthly, …). Recurrence is only none/daily/weekly (baseline probe #9).
    if unsupported_cadence_claim(reply, tool_names_used):
        violations.append("unsupported_cadence_claim")

    # Forward-tense mutation promise with no mutating tool called this turn.
    # "I'll edit the daily-LeetCode skill to add explanations." with zero
    # tool calls is the same lie as a retry promise — the turn ends and the
    # edit never happens. Only when tools are available (not Q&A-only) and
    # the reply isn't a clarifying question (no '?').
    if (
        tools_available
        and "?" not in reply
        and not (tool_names_used & _MUTATING_TOOLS)
        and _GUARD_MUTATION_PROMISE_RE.search(reply)
    ):
        violations.append("false_mutation_promise")

    if not violations:
        return reply, []

    try:
        events.emit(
            "output_guard",
            violations=",".join(violations),
            preview=reply[:100].replace("\n", " "),
        )
    except Exception:
        pass
    log.warning(f"[output_guard] blocked reply ({violations}): {reply[:80]!r}")
    return None, violations


# --- Correction prompts ------------------------------------------------------
# One per violation family. Injected as a synthetic user turn by
# Agent._self_correct, then pruned from history after the retry.

_SELF_CORRECTION_PROMPT = (
    "Your previous reply mentioned internal file paths or system error strings "
    "that should not be shown to the user. Please restate your answer using "
    "plain language only — no filenames, no *.md paths, no ERROR prefixes. "
    "Describe what you do in terms the user understands."
)

_ACTION_CLAIM_CORRECTION_PROMPT = (
    "You just said you performed an action (created a task, sent a notification, etc.) "
    "but you did NOT call any tools. That is a hallucination — you cannot perform actions "
    "through text alone. You MUST call the appropriate tool now to actually do what "
    "the user asked. Do not explain — just call the tool."
)

_CLAIM_INCONSISTENT_CORRECTION_PROMPT = (
    "Your previous reply claimed you successfully read, fetched, or wrote a file or URL, "
    "but the tool calls in this turn against that target ALL returned errors. Do not "
    "fabricate success. Restate honestly what you tried and what failed, naming the "
    "actual error. If the user needs the action attempted differently, say so — do not "
    "pretend it succeeded."
)

_SUCCESS_CLAIM_CORRECTION_PROMPT = (
    "Your previous reply claimed an action succeeded (filed/created/sent/saved/"
    "proposed), but the tool that would have performed it FAILED this turn "
    '(e.g. propose_skill returned {"ok": false} with errors). Do not pretend '
    "it worked just because other tool calls succeeded. Read the tool error, "
    "tell the user plainly what failed and why, and either fix the inputs and "
    "call the tool again now, or say it didn't go through. Never report success "
    "for a failed call."
)

_ARTIFACT_CLAIM_CORRECTION_PROMPT = (
    "Your previous reply pointed the user at a proposal that does not exist — "
    "you cited a prop-NNNN ID or said you 'filed it' / it 'awaits approval on "
    "the Overview page', but propose_skill did not successfully run this turn, "
    "so there is nothing for the user to approve. NEVER invent a proposal ID or "
    "claim a filing that didn't happen — the user will go looking and find "
    "nothing. Either actually call propose_skill now and report the REAL id it "
    "returns, or tell the user plainly that you have not filed anything yet."
)

_FUTURE_PROMISE_CORRECTION_PROMPT = (
    "Your previous reply ended the turn with a promise of immediate next action "
    "('I will try X next', 'Let me check Y', 'I'll search again'). This is dishonest: "
    "your turn is over after you reply. If you genuinely intend to take that action, "
    "DO IT NOW by calling the appropriate tool — don't say 'I will' and then stop. If "
    "you're declining to continue, say so plainly: 'I tried X, Y, Z and they all "
    "failed; I'm not going to keep guessing — tell me where to look.' Never promise "
    "follow-up work you won't perform."
)

_MUTATION_PROMISE_CORRECTION_PROMPT = (
    "Your previous reply said you WILL edit/create/update a skill, task, or "
    "file (e.g. 'I'll edit the skill to add ...'), but you did not call any "
    "tool that performs that change — and your turn ends after you reply, so "
    "the change will NEVER happen. The user asked for it, so DO IT NOW: call "
    "propose_skill (to change a skill), create_task / update_task (for a "
    "task), or the appropriate tool, and report the REAL result it returns. "
    "If you genuinely need one detail first, ask that question plainly — do "
    "not promise an edit you won't make."
)

_UNSUPPORTED_CADENCE_CORRECTION_PROMPT = (
    "Your previous reply promised a schedule the task system cannot do. "
    "Recurrence supports ONLY none, daily, or weekly — never weekday-only, "
    "skip-holidays, skip-weekends, every-other-day, or monthly. Restate "
    "honestly: tell the user the recurrence you actually set (e.g. 'a daily "
    "7am reminder') and plainly name the part you can't do plus a workaround "
    "(e.g. 'I can't auto-skip public holidays — pause it on those days'). "
    "Do not claim an unsupported cadence, and do not create extra tasks to "
    "fake one."
)

_UNGROUNDED_URL_CORRECTION_PROMPT = (
    "Your previous reply cited one or more web links that did NOT come from "
    "your tool results this turn — you invented or guessed them. Never "
    "fabricate URLs. Restate your answer citing ONLY links that appear "
    "verbatim in your web_search / web_fetch results. For any claim or "
    "number you cannot back with a real fetched source, either drop it or "
    "say plainly that you couldn't verify it. Do not present unverified "
    "figures as fact."
)

# Violation → correction prompt, most specific first. Ordering matters where
# violations co-occur: the action/success families carry tool-calling
# instructions, so they win over the generic path-leak restatement.
_CLAIMS_DRAFTING_DONE_RE = __import__("re").compile(
    r"\b(all|every)\b.{0,60}\b(drafted|saved|answered|complete[d]?)\b",
    __import__("re").IGNORECASE | __import__("re").DOTALL,
)

_DRAFTING_INCOMPLETE_CORRECTION_PROMPT = (
    "Your reply claims the drafting is complete, but the last "
    "draft_answer result still listed unanswered questions. Nothing you "
    "narrate is saved. Call draft_answer for EACH remaining question "
    "now, one at a time, until the tool says the plan is complete — "
    "then reply."
)

_TOOL_SYNTAX_LEAK_CORRECTION_PROMPT = (
    "Your reply contains raw tool-call markup (<|channel|>/to=functions...) "
    "as plain text — that call was NEVER executed, and nothing it claimed "
    "happened. Re-issue the operation as a proper tool call (one call, no "
    "markup in your text), wait for its result, then answer based on what "
    "actually happened. Do not claim work is saved unless the tool result "
    "confirmed it."
)

_CORRECTION_PROMPTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tool_syntax_leak",), _TOOL_SYNTAX_LEAK_CORRECTION_PROMPT),
    (("drafting_completion_claim",), _DRAFTING_INCOMPLETE_CORRECTION_PROMPT),
    (("action_claim_without_tool_call",), _ACTION_CLAIM_CORRECTION_PROMPT),
    (
        ("success_claim_all_tools_failed", "success_claim_tool_failed"),
        _SUCCESS_CLAIM_CORRECTION_PROMPT,
    ),
    (("unverified_artifact_claim",), _ARTIFACT_CLAIM_CORRECTION_PROMPT),
    (("claim_inconsistent_with_tool_result",), _CLAIM_INCONSISTENT_CORRECTION_PROMPT),
    (("false_future_promise",), _FUTURE_PROMISE_CORRECTION_PROMPT),
    (("false_mutation_promise",), _MUTATION_PROMISE_CORRECTION_PROMPT),
    (("ungrounded_url",), _UNGROUNDED_URL_CORRECTION_PROMPT),
    (("unsupported_cadence_claim",), _UNSUPPORTED_CADENCE_CORRECTION_PROMPT),
)


def correction_prompt_for(violations: list[str] | None) -> str:
    """The retry instruction matching the first (most specific) violation
    family present; the generic path-leak restatement when none match."""
    for family, prompt in _CORRECTION_PROMPTS:
        if violations and any(v in violations for v in family):
            return prompt
    return _SELF_CORRECTION_PROMPT
