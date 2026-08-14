"""Trajectory evals — deterministic scoring of past task runs.

The rest of the harness answers two questions: did the reply hold up
(output_guard), and did the task actually complete (TaskGuard +
settlement). Neither answers a third question: when a run *did*
complete, how cleanly did it get there? Two runs of the same skill can
both end in complete_task, but one took 2 clean tool calls and the
other took 6 with 3 tool-choice violations along the way — that
difference is invisible unless someone reads the trace by hand.

This module scores that difference, retrospectively, over runs that
already happened. No new LLM calls, no budget impact — pure functions
over `tasks.json` run history and `_events.jsonl`, in the same spirit
as stats.py: one source of truth, testable without mocking the
filesystem.

The golden reference is never invented here — it's whatever the skill
itself already declares:
  - a `states:` skill (skills.load_skill_playbook) has an explicit
    ordered tool sequence, scored by LCS subsequence match (the
    `agentevals`-standard method: the pinned sequence must appear, in
    order, as a subsequence of the actual trace — harness-injected
    calls like load_tool or a read-cache hit between pinned tools
    don't break the match, only an out-of-order or missing pinned
    tool does).
  - a `requires_tools` skill (skills.load_skill_requires_tools) has an
    unordered set of tools that must appear anywhere in the trace.

Known limitation, surfaced by the first live run against real data:
a contract is read from the skill file as it exists TODAY, not as it
was when a given run happened. Quiz-coach's `states:` block was added
2026-08-01 after five silent-drop incidents; scoring is retroactive,
so every pre-fix run correctly shows as non-compliant against a
contract that didn't exist yet — that's honest (there was genuinely no
discipline being enforced then), not a bug, but it means "compliance
rate" over a long window blends eras rather than isolating "since the
fix." Not solved here — would need a contract version snapshotted per
run, real scope for a later pass, not this one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VIOLATION_EVENTS = frozenset({"required_tool_violation", "wrong_forced_tool"})

# `output_guard` is an overloaded event name: core.py emits it for tool-dispatch
# guards (five distinct kinds) and output_guard.py for a blocked reply. Each
# emitter tags itself with `kind`, so the classification below is structural.
#
# A cache hit is excluded because it is not a model defect the scorecard should
# charge for — the harness served a repeated read-only call from cache, saving a
# round trip. Every other kind is the harness correcting something the model got
# wrong, which is exactly the signal a model comparison wants.
_BENIGN_GUARD_KINDS = frozenset({"cache_hit"})

# Events written before `kind` existed carry only a human-readable `text`.
# Matching prose is fragile, which is why it is confined to this fallback and
# never used for events that do declare a kind.
_LEGACY_BENIGN_TEXT = ("cache hit",)


def _is_scored_guard_fire(event: dict[str, Any]) -> bool:
    """Whether an `output_guard` event counts against the model.

    Prefers the structural `kind` tag; falls back to the legacy text match only
    for events emitted before that tag existed, so historical traces keep
    scoring the same way they did when they were written.
    """
    kind = event.get("kind")
    if kind is not None:
        return str(kind) not in _BENIGN_GUARD_KINDS
    text = str(event.get("text", ""))
    return not any(marker in text for marker in _LEGACY_BENIGN_TEXT)


@dataclass(frozen=True)
class Contract:
    """A skill's golden reference, in whichever shape it declared.

    Exactly one of `states` / `requires_tools` is non-empty — a skill
    is one kind or the other, never both (load_contract enforces this
    by construction: states takes priority when a skill has both).
    """

    states: tuple[str, ...] = ()
    requires_tools: frozenset[str] = frozenset()

    @property
    def kind(self) -> str:
        if self.states:
            return "states"
        if self.requires_tools:
            return "requires_tools"
        return "none"


def load_contract(memory_root: Path, skill_name: str) -> Contract:
    """Read a skill's contract straight from its frontmatter — the same
    data heartbeat._plan_tick already reads to build the state machine
    and the capability gate. Nothing new is declared here."""
    from homunculus.skills import load_skill_playbook, load_skill_requires_tools

    try:
        states, _body = load_skill_playbook(memory_root, skill_name)
    except FileNotFoundError:
        states = None
    if states:
        tools = tuple(s["tool"] for s in states if isinstance(s, dict) and "tool" in s)
        if tools:
            return Contract(states=tools)

    required = load_skill_requires_tools(memory_root, skill_name)
    return Contract(requires_tools=frozenset(required))


def _is_subsequence(needle: tuple[str, ...], haystack: list[str]) -> bool:
    """True if `needle` appears, in order, as a subsequence of `haystack`
    (LCS subsequence match — extra items in between are ignored)."""
    it = iter(haystack)
    return all(want in it for want in needle)


@dataclass(frozen=True)
class RunScore:
    """One run, scored against its skill's contract."""

    ts: str
    status: str
    contract_kind: str
    contract_compliance: bool
    violations: int
    guard_fires: int
    calls: int
    expected_calls: int | None
    cost_cents: float
    #: model_id that ran it, "" for runs recorded before this field
    #: existed. Lets a scorecard split its history by model instead of
    #: blending every model the skill has ever run under into one number.
    model: str = ""


def score_run(contract: Contract, run: dict[str, Any], events_window: list[dict]) -> RunScore:
    """Score one `tasks.json` run record against its skill's contract.

    `events_window` is the slice of parsed _events.jsonl records
    between the previous run's timestamp (or task creation) and this
    run's — the caller does the windowing (see score_skill), this
    function only scores what it's handed.
    """
    trace = [t.strip() for t in str(run.get("tool_trace") or "").split(",") if t.strip()]

    if contract.kind == "states":
        compliance = _is_subsequence(contract.states, trace)
        expected_calls: int | None = len(contract.states)
    elif contract.kind == "requires_tools":
        compliance = contract.requires_tools.issubset(set(trace))
        expected_calls = None
    else:
        # No declared contract — nothing to check compliance against;
        # still worth recording violations/cost for the trend.
        compliance = True
        expected_calls = None

    violations = sum(1 for e in events_window if e.get("event") in _VIOLATION_EVENTS)
    guard_fires = sum(
        1 for e in events_window
        if e.get("event") == "output_guard" and _is_scored_guard_fire(e)
    )

    return RunScore(
        ts=str(run.get("ts", "")),
        status=str(run.get("status", "")),
        contract_kind=contract.kind,
        contract_compliance=compliance,
        violations=violations,
        guard_fires=guard_fires,
        calls=int(run.get("calls") or 0),
        expected_calls=expected_calls,
        cost_cents=float(run.get("cost_cents") or 0.0),
        model=str(run.get("model") or ""),
    )


@dataclass(frozen=True)
class ModelSlice:
    """The same aggregate numbers as SkillScorecard, scoped to runs that
    happened to run under one model — how SkillScorecard.by_model groups
    its history when a skill has lived through more than one model."""

    model: str
    run_scores: tuple[RunScore, ...]

    @property
    def runs(self) -> int:
        return len(self.run_scores)

    @property
    def compliance_rate(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(1 for r in self.run_scores if r.contract_compliance) / self.runs

    @property
    def avg_violations(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.violations for r in self.run_scores) / self.runs

    @property
    def avg_guard_fires(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.guard_fires for r in self.run_scores) / self.runs

    @property
    def avg_cost_cents(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.cost_cents for r in self.run_scores) / self.runs


@dataclass(frozen=True)
class SkillScorecard:
    """Aggregate score for one task/skill over its recent run history."""

    task_id: str
    contract_kind: str
    run_scores: tuple[RunScore, ...] = field(default_factory=tuple)

    @property
    def runs(self) -> int:
        return len(self.run_scores)

    @property
    def compliance_rate(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(1 for r in self.run_scores if r.contract_compliance) / self.runs

    @property
    def avg_violations(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.violations for r in self.run_scores) / self.runs

    @property
    def avg_guard_fires(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.guard_fires for r in self.run_scores) / self.runs

    @property
    def avg_cost_cents(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.cost_cents for r in self.run_scores) / self.runs

    @property
    def trend(self) -> str:
        """'improving' / 'steady' / 'degrading' by comparing mean
        violations in the older half of the window to the newer half —
        same week-over-week-diff spirit as report.week_in_review, just
        scoped to one skill instead of the whole app. Needs at least 4
        runs to say anything; fewer than that is 'insufficient_data'."""
        if self.runs < 4:
            return "insufficient_data"
        mid = self.runs // 2
        older = self.run_scores[:mid]
        newer = self.run_scores[mid:]
        older_avg = sum(r.violations for r in older) / len(older)
        newer_avg = sum(r.violations for r in newer) / len(newer)
        if newer_avg < older_avg - 0.5:
            return "improving"
        if newer_avg > older_avg + 0.5:
            return "degrading"
        return "steady"

    @property
    def by_model(self) -> dict[str, ModelSlice]:
        """Split run history by the model that ran each run, in first-
        seen order — so a model swap shows up as two distinct slices
        instead of one blended average. Runs recorded before `model`
        was tracked group under "" (rendered "unknown" by callers)."""
        order: list[str] = []
        buckets: dict[str, list[RunScore]] = {}
        for r in self.run_scores:
            if r.model not in buckets:
                order.append(r.model)
                buckets[r.model] = []
            buckets[r.model].append(r)
        return {m: ModelSlice(model=m, run_scores=tuple(buckets[m])) for m in order}


def _events_between(events: list[dict], start_ts: str | None, end_ts: str) -> list[dict]:
    """Events with ts in (start_ts, end_ts] — start_ts=None means
    "since the beginning of the log"."""
    out = []
    for e in events:
        ts = str(e.get("ts", ""))
        if not ts or ts > end_ts:
            continue
        if start_ts is not None and ts <= start_ts:
            continue
        out.append(e)
    return out


def score_skill(
    task_id: str, contract: Contract, runs: list[dict], events: list[dict],
) -> SkillScorecard:
    """Score every run in a task's `last_runs` history against its
    skill's contract, windowing events between consecutive runs."""
    ordered = sorted(runs, key=lambda r: str(r.get("ts", "")))
    scores = []
    prev_ts: str | None = None
    for run in ordered:
        end_ts = str(run.get("ts", ""))
        window = _events_between(events, prev_ts, end_ts)
        scores.append(score_run(contract, run, window))
        prev_ts = end_ts
    return SkillScorecard(task_id=task_id, contract_kind=contract.kind, run_scores=tuple(scores))


def score_all(
    tasks: list[dict], events: list[dict], memory_root: Path,
) -> dict[str, SkillScorecard]:
    """Score every task that's linked to a skill. Tasks with no `skill`
    field are skipped — there's no contract to score them against."""
    out: dict[str, SkillScorecard] = {}
    for task in tasks:
        skill_name = task.get("skill")
        if not skill_name:
            continue
        contract = load_contract(memory_root, skill_name)
        out[task["id"]] = score_skill(task["id"], contract, task.get("last_runs") or [], events)
    return out


def load_events(events_path: Path) -> list[dict]:
    """Parse _events.jsonl, skipping any line that doesn't parse —
    mirrors events.rotate's own tolerance for a mid-append final line."""
    if not events_path.is_file():
        return []
    out = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_tasks(tasks_path: Path) -> list[dict]:
    """Parse tasks.json in either shape TaskStore has ever written
    ({"tasks": [...]}, or a bare list)."""
    if not tasks_path.is_file():
        return []
    data = json.loads(tasks_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("tasks") or [])
    return list(data)


__all__ = [
    "Contract",
    "RunScore",
    "ModelSlice",
    "SkillScorecard",
    "load_contract",
    "score_run",
    "score_skill",
    "score_all",
    "load_events",
    "load_tasks",
]
