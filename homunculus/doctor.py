"""Startup posture audit — surfaces drift in stored config. Never blocks.

Validation at write time only protects what is written after the rule exists.
Everything already on disk keeps whatever shape it had, and nothing looks at
it again: a task scheduled last month is never re-examined, so a weakness
introduced before a rule — or one deliberately allowed and then forgotten —
stays invisible until it costs something.

That is how a six-day mail outage read as six successful deliveries. The
task's criteria could not tell a delivery from an apology, they had been
written that way months earlier, and no code path ever asked again.

So this module asks again, at process start. It is **advisory**: every check
returns human-readable findings and logs a warning, and none of them raise or
block startup. An audit that can stop the agent booting is an audit an
operator eventually deletes.

The shape is borrowed from tools that had the same problem — Hermes's
`security_audit_startup` (warn-on-load posture checks, each independent and
fail-safe) and OpenClaw's `doctor` flows (auditing stored state against
current schema). Both converge on: report, don't enforce.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from homunculus.skill_validation import criteria_strength_errors

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    """One thing an operator might want to know. `subject` identifies what."""

    check: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.subject}: {self.detail}"



# Criteria that prove real data was fetched, whatever their parameters: a
# grounded link came from a tool result this run, a code fence and a unique
# key cannot be produced by an apology.
_STRUCTURAL_PROOF = {"notify_links_grounded", "notify_has_code", "notify_unique"}


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _has_discriminating_marker(criteria: list[dict], task: dict) -> bool:
    """Whether any criterion could actually fail on a failure notice.

    A `notify_contains` earns its keep only when the string it demands comes
    from fetched data. When it merely repeats the task's own title, the agent
    writes that header whether the fetch succeeded or not — "📅 Event watch —
    Email not connected" satisfies `notify_contains: "Event watch"` exactly as
    well as a real delivery does. A marker like "Totals:", which only appears
    once the profile call returned numbers, does discriminate.
    """
    identity = _normalise(f"{task.get('title', '')} {task.get('id', '')}")
    for c in criteria:
        ctype = c.get("type")
        if ctype in _STRUCTURAL_PROOF:
            return True
        if ctype in ("notify_contains", "notify_matches"):
            marker = _normalise(str(c.get("text") or c.get("pattern") or ""))
            if marker and marker not in identity:
                return True
    return False


def audit_task_criteria(tasks: list[dict]) -> list[Finding]:
    """Report scheduled tasks whose success criteria cannot fail.

    A criterion set that an outage notice would satisfy makes the task report
    success forever — the skill never looks broken, and the reflection loop
    gets no failure to reason about.

    Two mitigations are recognised, because either is enough on its own:

    * criteria strong enough that a failure notice would not pass them; or
    * a declared data source, which `TaskGuard.every_required_source_failed`
      gates independently of the criteria.

    A task with neither is reported. A task protected only by the source gate
    is reported at lower volume — it is defended, but its criteria are load-
    bearing nowhere, which is worth knowing before someone edits the skill.
    """
    findings: list[Finding] = []
    for task in tasks:
        if task.get("status") not in (None, "active", "pending"):
            continue
        criteria = task.get("success_criteria") or []
        if not criteria:
            continue
        weak = criteria_strength_errors(criteria, where="success_criteria")
        if not weak:
            continue
        # A short length floor is fine when something else in the criteria
        # could not survive a failed run.
        if _has_discriminating_marker(criteria, task):
            continue
        task_id = str(task.get("id") or "?")
        # `skill` implies declared `requires_tools`, which the TaskGuard gates.
        if task.get("skill"):
            findings.append(Finding(
                "task_criteria",
                task_id,
                "criteria alone cannot distinguish a delivery from a failure "
                "notice; the task is currently protected only by its "
                f"data-source gate ({task['skill']}). "
                + weak[0].split(": ", 1)[-1],
            ))
        else:
            findings.append(Finding(
                "task_criteria",
                task_id,
                "criteria cannot distinguish a delivery from a failure notice, "
                "and the task declares no data source to gate on. "
                + weak[0].split(": ", 1)[-1],
            ))
    return findings


# Tools every skill uses to do its housekeeping. They are not data sources:
# notify delivers whatever was gathered, complete_task closes the run, the rest
# are harness plumbing. A skill that "requires" them declares nothing about
# where its content comes from, which is the only thing the source gate can
# usefully protect.
_HARNESS_TOOLS = frozenset({
    "notify", "complete_task", "record_failure", "cancel_task", "continue_task",
    "load_tool", "task_scratchpad", "get_current_time", "read_file", "write_file",
    "recall", "remember", "forget", "list_tasks", "list_proposals", "propose_skill",
    "update_world_state", "record_commitment", "web_fetch",
})

# Below this, "every run used it" is coincidence rather than a pattern.
_MIN_RUNS_FOR_SOURCE_CLAIM = 3


def _tools_used_every_successful_run(task: dict) -> tuple[set[str], int]:
    """Domain tools present in EVERY successful run, and how many runs that was.

    Intersection, not frequency: a tool the skill genuinely depends on appears
    every time it succeeds. Anything less is a tool it sometimes reaches for,
    which is not something to gate a delivery on.
    """
    per_run: list[set[str]] = []
    for run in task.get("last_runs") or []:
        if run.get("status") != "success" or run.get("dry_run"):
            continue
        trace = str(run.get("tool_trace") or "")
        if not trace:
            continue
        names = {
            part.strip().split(" \u00d7")[0].strip()
            for part in trace.split(",")
        }
        per_run.append({n for n in names if n and n not in _HARNESS_TOOLS})
    if not per_run:
        return set(), 0
    common = set.intersection(*per_run) if len(per_run) > 1 else per_run[0]
    return common, len(per_run)


def audit_undeclared_sources(tasks: list[dict], memory_root: Path | None) -> list[Finding]:
    """Report skills that depend on a data source they never declared.

    `TaskGuard.every_required_source_failed` is what stopped six days of
    "Email not connected" counting as six successful deliveries -- but it reads
    the skill's declared `requires_tools`, and returns immediately when a skill
    declares none. A skill that calls one tool on every single run and declares
    nothing therefore has no source gate at all: when that tool starts failing,
    the outage notice is recorded as a success, exactly as before.

    Undeclared is the interesting direction. A declared tool that goes unused
    is noisy but harmless; an undeclared dependency is an ungated one.
    """
    if memory_root is None:
        return []
    from homunculus.skills import load_skill_requires_tools

    findings: list[Finding] = []
    for task in tasks:
        if task.get("status") not in (None, "active", "pending"):
            continue
        skill = task.get("skill")
        if not skill:
            continue
        used, run_count = _tools_used_every_successful_run(task)
        if not used or run_count < _MIN_RUNS_FOR_SOURCE_CLAIM:
            continue
        try:
            declared = set(load_skill_requires_tools(memory_root, str(skill)))
        except (OSError, ValueError):
            continue
        undeclared = sorted(used - declared)
        if not undeclared:
            continue
        findings.append(Finding(
            "undeclared_source",
            str(task.get("id") or "?"),
            f"{', '.join(undeclared)} used in all {run_count} successful runs but not "
            f"declared in {skill}'s requires_tools, so the source gate cannot see it — "
            "if it starts failing, the outage notice will be recorded as a success.",
        ))
    return findings


def run_startup_audit(
    tasks: list[dict], memory_root: Path | None = None,
) -> list[Finding]:
    """Run every check and log what it found. Returns the findings.

    Each check is isolated: one raising must not deny an operator the results
    of the others, and must never take the process down.
    """
    findings: list[Finding] = []
    checks: tuple[tuple[str, Callable[[], list[Finding]]], ...] = (
        # Resolved through the module globals at call time so a test can
        # substitute one and still exercise the isolation below.
        ("audit_task_criteria", lambda: audit_task_criteria(tasks)),
        ("audit_undeclared_sources", lambda: audit_undeclared_sources(tasks, memory_root)),
    )
    for name, check in checks:
        try:
            findings.extend(check())
        except Exception as e:  # a broken audit must not break the agent
            log.warning(f"[doctor] check {name} failed: {e}")

    if findings:
        log.warning(
            f"[doctor] {len(findings)} finding(s) — advisory, nothing is blocked:"
        )
        for f in findings:
            log.warning(f"[doctor]   {f}")
    return findings
