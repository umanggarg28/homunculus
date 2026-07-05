"""TaskGuard — the delivery guard for scheduled task runs.

Verifies a task's delivery against what tools actually did, never against
the model's narration. Attached as run-scoped hooks on the one Agent that
executes the task (pre_execute / post_execute / pre_turn on the Agent
constructor); heartbeat ticks and the web run-stream both build it via
heartbeat.build_task_guard, so scheduled and manual runs enforce the same
criteria.
"""

import re
from typing import Any


_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]}]+")
# Trailing punctuation that commonly clings to a URL in prose/markdown but
# isn't part of the address (e.g. "see https://x/a)." → strip ").").
_URL_TRAILING = ".,;:!?)]}>\"'"


def _extract_urls(text: str) -> list[str]:
    """Pull URLs out of free text, trimming trailing punctuation. Order-
    preserving and de-duplicated."""
    seen: dict[str, None] = {}
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_URL_TRAILING)
        if url:
            seen.setdefault(url, None)
    return list(seen)


# Tool-failure sentinels. Tools whose data source can fail return these
# machine tokens so the MODEL knows to omit the section (tools/news.py,
# tools/weather.py). They are signals on the tool→model channel, never
# user-facing content: a delivery carrying one means the model pasted a
# failure token verbatim — or hallucinated the failure branch without
# calling the tool at all — instead of omitting the section.
_FAILURE_SENTINELS = ("NEWS_UNAVAILABLE", "WEATHER UNAVAILABLE")


class TaskGuard:
    """Pi-style output guard for scheduled task delivery.

    Attached as run-scoped hooks to the one Agent that executes the task
    (pre_execute / post_execute / pre_turn on the Agent constructor), so it
    supervises exactly that run and nothing else. Intercepts two tool calls:

    - notify()        → refuses text carrying a tool-failure sentinel, then
                        records the text so criteria can inspect it.
    - complete_task() → checks that every tool the skill declares in
                        `requires_tools` was actually attempted this run,
                        then checks the task's success_criteria, before
                        allowing the call through. Returns a structured
                        BLOCKED error if any check fails, so the agent can
                        correct its output and retry rather than silently
                        recording garbage as a successful delivery.

    Supported criterion types
    ─────────────────────────
    {"type": "notify_called"}
        At least one notify() was called during this tick.

    {"type": "notify_min_chars", "n": N}
        The combined text of all notify() calls is at least N characters.

    {"type": "notify_has_code"}
        At least one notify() text contains a fenced code block (```).

    {"type": "notify_contains", "text": "..."}
        At least one notify() text contains the given substring.

    {"type": "notify_matches", "pattern": "..."}
        At least one notify() text matches the given regex.

    {"type": "notify_unique", "pattern": "..."}
        The notify text must contain a delivery key (regex group 1, or
        the whole match) that is NOT in the task's `delivered` ledger.
        Blocks re-sending content the user already received — checked
        deterministically at send time, not entrusted to the model's
        bookkeeping. The heartbeat records the key into the ledger via
        TaskStore.record_delivery once the notify has gone out.
    """

    def __init__(
        self,
        criteria_by_task: dict[str, list[dict[str, Any]]],
        delivered_by_task: dict[str, set[str]] | None = None,
        required_calls_by_task: dict[str, list[str]] | None = None,
    ) -> None:
        self._criteria = criteria_by_task
        # Lowercased delivery keys each task has already sent (from the
        # task's `delivered` ledger). Consulted by notify_unique.
        self._delivered = delivered_by_task or {}
        # Tools each task's skill declares in `requires_tools`. The
        # capability gate (_plan_tick) already guarantees they EXIST; this
        # guard enforces that they are EXERCISED — complete_task is refused
        # until every one was at least attempted this run. Closes the gap
        # where the model skips a declared data source and improvises its
        # output (e.g. pasting NEWS_UNAVAILABLE without calling
        # news_headlines).
        self._required_calls = required_calls_by_task or {}
        # Notify texts seen so far this tick — kept as a tracker only, NOT
        # as a delivery buffer. Every notify() call now sends immediately;
        # this list lets _check() validate criteria pre-send and lets
        # expected_remaining() report whether the agent ever called notify
        # at all. Previously the TaskGuard buffered notify and gated on
        # complete_task succeeding — which silently dropped every delivery
        # when the agent wrote "task complete" in prose instead of calling
        # the tool. The structural fix is: criteria are checked SYNCHRONOUSLY
        # at notify() call time. If they fail, notify is refused with a
        # retry instruction. The agent never gets to act on a buffered
        # message that might not deliver.
        self._notify_texts: list[str] = []
        # Track which due tasks have had complete_task called successfully.
        # Combined with expected_remaining(), the heartbeat tick can detect
        # the silent-failure mode (agent loop ran but no due task was
        # completed) and report a *specific* failure.
        self._completed_tasks: set[str] = set()
        # Ordered tool-call trace for the tick — the sequence of tool names the
        # agent invoked. Attributed to the run record so the daily reflection
        # can spot skill staleness that delivered_text/status can't (e.g. a tool
        # called repeatedly because the skill misreads its result).
        self._tool_trace: list[str] = []
        # Concatenated text of every tool RESULT this tick (minus notify's own
        # result). The notify_links_grounded criterion checks delivered URLs
        # against this — a link the agent didn't get from a tool is fabricated.
        self._tool_result_text: list[str] = []

    def on_tool_call(self, name: str, arguments: dict) -> str | None:
        """The Agent's pre_execute_hook for this run.

        Returns None to allow the call, or a non-empty string to block it
        and return that string as the tool result.
        """
        self._tool_trace.append(name)
        if name == "notify":
            text = str(arguments.get("text") or "")
            # Never deliver a tool-failure sentinel. These tokens instruct
            # the MODEL to omit a section; in a user-facing message they are
            # either a verbatim paste of a failure or (the observed case) a
            # fabricated failure branch — the model wrote the sentinel
            # without calling the tool at all.
            leaked = next((s for s in _FAILURE_SENTINELS if s in text), None)
            if leaked is not None:
                return (
                    f"BLOCKED: notify() not sent — the text contains the "
                    f"tool-failure sentinel {leaked!r}. Sentinels are "
                    f"machine signals from tool results, never content for "
                    f"the user. If you have not called the tool that "
                    f"produces this data, call it now and use its real "
                    f"output. If the tool itself failed, resend the message "
                    f"with that section omitted entirely."
                )
            # Check criteria across ALL due tasks. If any task's criteria
            # would fail with the combined text so far + this proposed
            # message, refuse the send and tell the agent what's missing.
            # This is the structural fix for "the buffered message died
            # when the agent skipped complete_task" — now nothing gets
            # buffered. Either notify delivers and counts, or it's blocked
            # and the agent retries before the loop ends.
            #
            # Criteria are evaluated against the combined notify texts
            # because some criteria (notify_min_chars, notify_has_code)
            # can be satisfied by an earlier notify in the same tick
            # plus this one — the historical behavior we want to preserve.
            tentative = self._notify_texts + [text]
            failures = self._check_against(tentative)
            if failures:
                return (
                    "BLOCKED: notify() not sent. Criteria failed:\n"
                    + "\n".join(f"  • {f}" for f in failures)
                    + "\n\nCall notify(text=...) again with content that "
                    "satisfies the failed criterion (e.g. add a fenced "
                    "```code block```, lengthen the message, include the "
                    "required substring). The retry will be sent if it "
                    "passes."
                )
            # Criteria pass — record the text and let the call through.
            # The MCP subprocess does the actual Telegram send.
            self._notify_texts.append(text)
            return None

        if name == "complete_task":
            task_id = arguments.get("task_id", "")
            # Gate completion on the skill's declared data sources having
            # been exercised. An ATTEMPT is enough — a source that errors
            # still lets the section degrade gracefully per the playbook;
            # what's refused is skipping the source and improvising.
            missing = self.missing_required_calls(task_id)
            if missing:
                return (
                    "ERROR: complete_task blocked — this task's skill "
                    "requires tool(s) you never called this run: "
                    + ", ".join(missing)
                    + ". Call each one and build the delivery from its real "
                    "output (omit a section only if its tool returns a "
                    "failure), send the corrected notify(text=...), then "
                    "call complete_task again. If the task genuinely cannot "
                    "proceed, call record_failure(task_id, reason) instead."
                )
            # Gate completion on the task's own criteria. notify()-time
            # checking (above) covers the "bad content" case, but nothing
            # stops the model from calling complete_task WITHOUT ever
            # calling notify — otherwise a task can close (e.g. "could not
            # fetch problem ...; task marked complete") with the user
            # receiving nothing. The result string must start with
            # "ERROR" — core.py's terminal-tool accounting treats any
            # other prefix as a successful close and exits the loop.
            failures = self.criteria_failures(task_id)
            if failures:
                return (
                    "ERROR: complete_task blocked — this task's delivery "
                    "criteria are not satisfied:\n"
                    + "\n".join(f"  • {f}" for f in failures)
                    + "\n\nDeliver the content with notify(text=...) first, "
                    "then call complete_task again. If delivery is impossible "
                    "right now, call record_failure(task_id, reason) instead. "
                    "Do NOT mark this task complete without delivering."
                )
            self._completed_tasks.add(task_id)
            return None

        # The other lifecycle tools also close out a task — track them so
        # the post-tick check doesn't treat an explicit close as a silent
        # drop (which would double-record a partial on the same run).
        if name in ("record_failure", "cancel_task", "continue_task"):
            task_id = arguments.get("task_id", "")
            if name == "record_failure" and task_id:
                # Mirror of the complete_task gate. That gate blocks a
                # completion the harness can prove undeserved; this blocks
                # a failure the harness can prove wrong — every declared
                # criterion satisfied by real delivered notifies AND every
                # required tool exercised means the run succeeded by the
                # task's own definition (observed close-out mode: deliver
                # fine, then pick record_failure as a generic "wrap up"
                # tool and stamp a false failure on a delivered run). Only
                # blocked when complete_task would definitely be allowed,
                # so the model can never be refused by both gates at once.
                criteria = self._criteria.get(task_id) or []
                if (
                    criteria
                    and not self.criteria_failures(task_id)
                    and not self.missing_required_calls(task_id)
                ):
                    return (
                        "ERROR: record_failure blocked — this run already "
                        "satisfied every success criterion for this task "
                        "(the delivery went out). A delivered task is a "
                        "success: call complete_task(task_id=...) instead. "
                        "record_failure is only for runs that could not "
                        "deliver."
                    )
            if task_id:
                self._completed_tasks.add(task_id)
            return None

        if name == "rate_skill":
            # The harness rates skills from the authoritative run verdict after
            # the tick (success vs record_failure), so a self-reported rating
            # here would double-count or fabricate. Suppress it during ticks.
            return (
                "Noted — skill outcomes for scheduled runs are recorded "
                "automatically from the task result; no rate_skill call needed."
            )

        return None  # all other tools pass through unmodified

    def observe_tool_result(self, name: str, result: str) -> None:
        """Post-execute hook: record what a tool returned this tick.

        Feeds the notify_links_grounded criterion. notify's own result is
        skipped — it's a delivery receipt, not a data source, so it can't be
        used to self-validate a fabricated link.
        """
        if name == "notify" or not result:
            return
        self._tool_result_text.append(result)

    def _grounded_blob(self) -> str:
        """All tool-result text seen this tick, for link-provenance checks."""
        return "\n".join(self._tool_result_text)

    def missing_required_calls(self, task_id: str) -> list[str]:
        """Required tools (the skill's `requires_tools`) not yet attempted
        this run, in declaration order. An attempt is any call — success or
        failure — so a data source that errors still lets its section
        degrade gracefully; only skipping the source entirely blocks
        completion."""
        attempted = set(self._tool_trace)
        return [t for t in self._required_calls.get(task_id, []) if t not in attempted]

    def criteria_failures(self, task_id: str) -> list[str]:
        """Failure descriptions for ONE task's criteria, checked against
        the notify texts actually sent this tick. Empty list = satisfied.
        Tasks with no criteria always pass (there is nothing to check)."""
        return self._check(self._criteria.get(task_id) or [], task_id=task_id)

    def delivery_key(self, task_id: str) -> str | None:
        """The delivery key contained in this tick's SENT notify texts,
        per the task's notify_unique criterion. None when the task has
        no notify_unique criterion or nothing matched. Sent texts are
        ground truth for what reached the user — this is what the
        heartbeat records into the task's `delivered` ledger."""
        combined = " ".join(self._notify_texts)
        for c in self._criteria.get(task_id) or []:
            if c.get("type") == "notify_unique" and c.get("pattern"):
                m = re.search(c["pattern"], combined, re.IGNORECASE)
                if m:
                    return (m.group(1) if m.groups() else m.group(0)).strip().lower()
        return None

    def combined_notify_text(self) -> str:
        """The full text the agent sent via notify() this run — what actually
        reached the user. The heartbeat retrofits this onto the run record
        (delivered_text) so the daily reflection can self-critique delivery
        QUALITY, not just pass/fail. Empty if nothing was sent."""
        return "\n\n".join(t for t in self._notify_texts if t)

    def tool_trace(self) -> str:
        """The tick's tool-call sequence as a compact, run-over-counted string,
        e.g. 'quiz_pick, notify ×4, recall, complete_task'. Consecutive repeats
        are collapsed with a count so the reflection sees retry storms at a
        glance. Empty if no tools ran."""
        if not self._tool_trace:
            return ""
        parts: list[tuple[str, int]] = []
        for name in self._tool_trace:
            if parts and parts[-1][0] == name:
                parts[-1] = (name, parts[-1][1] + 1)
            else:
                parts.append((name, 1))
        return ", ".join(n if c == 1 else f"{n} ×{c}" for n, c in parts)

    def expected_remaining(self) -> list[str]:
        """Task IDs that were due at the start of this tick but have not yet
        had complete_task (or record_failure) called.

        Used by heartbeat.tick() after the agent loop returns to produce a
        more specific diagnostic than the generic post-success check — and
        to drive the autonomous notify() pattern from item 8 of the plan.
        """
        return [tid for tid in self._criteria if tid not in self._completed_tasks]

    def on_pre_turn(self, turn_idx: int, _history: list) -> dict | None:
        """The Agent's pre_turn_hook for this run.

        Called at the start of every loop iteration with the 0-indexed
        turn number. Returns a synthetic user message to inject, or None
        for a no-op turn.

        Currently used for ONE thing: at iter MAX_TURNS-1 (turn 19 of 20),
        if any due task is still unfinished, force a final message demanding
        complete_task() or record_failure() before the loop hits the cap.
        This is the structural complement to the prompt tightening — instead
        of HOPING the model wraps up, the harness ORDERS it to.

        Imported locally to avoid a circular import at module load.
        """
        from homunculus.config import get_config  # local import — see docstring
        max_turns = get_config().loop.max_turns
        if turn_idx != max_turns - 1:
            return None
        remaining = self.expected_remaining()
        if not remaining:
            return None
        # Pick the first uncompleted task — the message is per-task explicit.
        task_id = remaining[0]
        return {
            "role": "user",
            "content": (
                f"HARNESS DIRECTIVE (last iteration): task '{task_id}' has "
                f"not yet had complete_task() OR record_failure() called. "
                f"You have exactly one tool call left.\n\n"
                f"Pick ONE of:\n"
                f"  ✓ complete_task(task_id='{task_id}', result='<one-line summary>')\n"
                f"  ✗ record_failure(task_id='{task_id}', reason='<one-line reason>')\n\n"
                f"Notifications you've already sent this tick have already "
                f"reached the user — complete_task just closes the lifecycle. "
                f"If you haven't been able to deliver useful content yet, "
                f"prefer record_failure with the reason. DO NOT call any "
                f"other tool."
            ),
        }

    def _check_against(self, texts: list[str]) -> list[str]:
        """Check all due tasks' criteria against a candidate notify-text list.

        Aggregates failures across every task that has criteria. The
        agent sees a single combined "you need X, Y, Z" message so it
        can craft one corrected notify call covering all of them rather
        than trial-and-erroring per criterion.

        Called from on_tool_call pre-send: `texts = self._notify_texts +
        [proposed_text]`. The candidate text is delivered only when
        this returns an empty list.
        """
        failures: list[str] = []
        for task_id, criteria in self._criteria.items():
            failures.extend(self._check(criteria, texts, task_id=task_id))
        return failures

    def _check(
        self,
        criteria: list[dict[str, Any]],
        texts: list[str] | None = None,
        task_id: str = "",
    ) -> list[str]:
        """Return a list of human-readable failure descriptions for one
        task's criteria. `texts` defaults to the recorded notify texts
        (for the silent-drop fallback path); pre-send checking passes
        an explicit candidate list. `task_id` keys the delivered-ledger
        lookup for notify_unique."""
        if texts is None:
            texts = self._notify_texts
        combined = " ".join(texts)
        failures = []

        for c in criteria:
            ctype = c.get("type", "")

            if ctype == "notify_called":
                if not texts:
                    failures.append("notify() was never called — the user received nothing")

            elif ctype == "notify_min_chars":
                n = int(c.get("n", 0))
                length = len(combined)
                if length < n:
                    failures.append(
                        f"notify text too short ({length} chars, need ≥ {n}) — "
                        "add the full problem statement and solution"
                    )

            elif ctype == "notify_has_code":
                if "```" not in combined:
                    failures.append(
                        "notify text contains no code block (```) — "
                        "include a working code solution"
                    )

            elif ctype == "notify_contains":
                required = c.get("text", "")
                if required and required not in combined:
                    failures.append(
                        f"notify text does not contain required string: {required!r}"
                    )

            elif ctype == "notify_matches":
                pattern = c.get("pattern", "")
                if pattern and not re.search(pattern, combined, re.IGNORECASE):
                    failures.append(
                        f"notify text does not match required pattern: {pattern!r}"
                    )

            elif ctype == "notify_unique":
                pattern = c.get("pattern", "")
                if pattern:
                    m = re.search(pattern, combined, re.IGNORECASE)
                    if not m:
                        failures.append(
                            f"notify text contains no delivery key matching "
                            f"{pattern!r} — include the canonical link/identifier"
                        )
                    else:
                        key = (m.group(1) if m.groups() else m.group(0)).strip().lower()
                        if key in self._delivered.get(task_id, set()):
                            failures.append(
                                f"{key!r} was already delivered on a previous "
                                f"run — send the NEXT undelivered item instead "
                                f"(the task block lists already_delivered keys)"
                            )

            elif ctype == "notify_links_grounded":
                # Every URL in the delivery must appear verbatim in a tool
                # result this run. Catches the fabricated-link failure mode —
                # the model skips the fetch tool and invents placeholder
                # (example.com) or hallucinated links that pass a shape check
                # like notify_matches. We verify against the actual tool
                # output, never the model's narration.
                grounded = self._grounded_blob()
                for url in _extract_urls(combined):
                    if url not in grounded:
                        failures.append(
                            f"notify text contains a link no tool returned this "
                            f"run: {url} — every URL must be pasted verbatim from "
                            f"a tool result (e.g. news_headlines). Do not write, "
                            f"shorten, or invent links."
                        )

            else:
                # Unknown criterion type — skip rather than hard-fail so
                # adding new types doesn't break existing tasks.
                pass

        return failures
