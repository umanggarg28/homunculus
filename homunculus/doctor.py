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
from dataclasses import dataclass

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


def run_startup_audit(tasks: list[dict]) -> list[Finding]:
    """Run every check and log what it found. Returns the findings.

    Each check is isolated: one raising must not deny an operator the results
    of the others, and must never take the process down.
    """
    findings: list[Finding] = []
    for check in (audit_task_criteria,):
        try:
            findings.extend(check(tasks))
        except Exception as e:  # a broken audit must not break the agent
            log.warning(f"[doctor] check {check.__name__} failed: {e}")

    if findings:
        log.warning(
            f"[doctor] {len(findings)} finding(s) — advisory, nothing is blocked:"
        )
        for f in findings:
            log.warning(f"[doctor]   {f}")
    return findings
