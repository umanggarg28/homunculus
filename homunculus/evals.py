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
from collections import Counter
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


def _is_reply_block(event: dict[str, Any]) -> bool:
    """Whether this event is the delivery verifier refusing to send a reply.

    Untagged historical events are identified by the `violations` field, which
    only `output_guard.py` has ever written — the tool-dispatch guards in
    `core.py` do not carry it.
    """
    if event.get("kind") is not None:
        return str(event.get("kind")) == "reply_blocked"
    return "violations" in event


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
    #: Replies the delivery verifier refused to send (`kind="reply_blocked"`),
    #: a subset of `guard_fires`. Tracked apart on severity, not category: a
    #: blocked reply means the model claimed work it had no tool evidence for
    #: — the worst thing this system can do, and the rarest. Averaged in with
    #: hundreds of loop fires a single one would not visibly move the number,
    #: so it is carried as its own count and surfaced only when non-zero.
    reply_blocks: int = 0
    #: Guard events counted for this run that carried no task attribution.
    #: They predate task stamping, so they may belong to any task the
    #: heartbeat was running at the time. Non-zero means `guard_fires` is a
    #: time-window figure, not a per-task one, and comparisons must not score
    #: on it.
    unattributed_guards: int = 0
    #: Which version of the task's skill produced this run, 0 when the skill
    #: has no version history (or the run predates version stamping). Lets a
    #: scorecard compare the runs before a skill edit against those after it.
    skill_version: int = 0


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
    reply_blocks = sum(
        1 for e in events_window
        if e.get("event") == "output_guard" and _is_reply_block(e)
    )

    return RunScore(
        ts=str(run.get("ts", "")),
        status=str(run.get("status", "")),
        contract_kind=contract.kind,
        contract_compliance=compliance,
        violations=violations,
        guard_fires=guard_fires,
        reply_blocks=reply_blocks,
        skill_version=int(run.get("skill_version") or 0),
        unattributed_guards=sum(
            1 for e in events_window
            if e.get("event") == "output_guard" and _is_scored_guard_fire(e) and not e.get("task")
        ),
        calls=int(run.get("calls") or 0),
        expected_calls=expected_calls,
        cost_cents=float(run.get("cost_cents") or 0.0),
        model=str(run.get("model") or ""),
    )


class _RunAggregate:
    """Metrics over a set of scored runs.

    A mixin, not a base dataclass: each aggregate below declares its own
    `run_scores` field with its own ordering, and inheriting a field here
    would constrain that. Every aggregate answers the same questions, so the
    answers live in one place — they drifted when they didn't.
    """

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
    def reply_blocks(self) -> int:
        """Total refused replies, not an average.

        This number is meant to be read as "has this ever happened, and how
        often" — dividing a rare, severe event by the run count would round it
        toward invisibility, which is the failure mode this field exists to
        avoid.
        """
        return sum(r.reply_blocks for r in self.run_scores)

    @property
    def avg_cost_cents(self) -> float | None:
        if not self.run_scores:
            return None
        return sum(r.cost_cents for r in self.run_scores) / self.runs


@dataclass(frozen=True)
class ModelSlice(_RunAggregate):
    """The same aggregate numbers as SkillScorecard, scoped to runs that
    happened to run under one model — how SkillScorecard.by_model groups
    its history when a skill has lived through more than one model."""

    model: str
    run_scores: tuple[RunScore, ...]



@dataclass(frozen=True)
class SkillScorecard(_RunAggregate):
    """Aggregate score for one task/skill over its recent run history."""

    task_id: str
    contract_kind: str
    run_scores: tuple[RunScore, ...] = field(default_factory=tuple)


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

    @property
    def by_version(self) -> dict[int, VersionSlice]:
        """Split run history by the skill version that produced each run,
        ascending. Runs from before version stamping group under 0, which
        `compare_versions` excludes — an unstamped run belongs to no known
        version and must not be attributed to one."""
        buckets: dict[int, list[RunScore]] = {}
        for r in self.run_scores:
            buckets.setdefault(r.skill_version, []).append(r)
        return {
            v: VersionSlice(version=v, run_scores=tuple(buckets[v]))
            for v in sorted(buckets)
        }


@dataclass(frozen=True)
class VersionSlice(_RunAggregate):
    """The runs that executed under one version of a skill.

    The skill-edit twin of ModelSlice: a scorecard that blends the runs before
    an edit with the runs after it cannot show whether the edit helped.
    """

    version: int
    run_scores: tuple[RunScore, ...]


# A verdict needs enough runs on both sides to mean anything. Three is not
# statistical significance -- it is the point below which a single bad news
# day or a slow API would dominate the comparison. Below it the comparison
# reports "inconclusive" rather than a number that invites a wrong decision.
_MIN_RUNS_FOR_VERDICT = 3

# Metric weights for the headline score. Compliance dominates because it is
# the only metric tied to what the skill promised to do; cost is a tiebreak,
# never a reason to prefer a worse delivery.
_SCORE_WEIGHTS = {"compliance_rate": 3.0, "avg_guard_fires": 2.0, "avg_cost_cents": 1.0}

# Direction each metric should move for the edit to count as an improvement.
_HIGHER_IS_BETTER = {"compliance_rate": True, "avg_guard_fires": False, "avg_cost_cents": False}

_METRIC_LABELS = {
    "compliance_rate": "contract compliance",
    "avg_guard_fires": "guard fires per run",
    "avg_cost_cents": "cost per run",
}


@dataclass(frozen=True)
class MetricDelta:
    """One metric, before and after, with its direction accounted for."""

    name: str
    label: str
    before: float | None
    after: float | None
    #: Signed percent change from `before`. None when before is 0 or missing —
    #: percent change from zero is undefined, and reporting it as a large
    #: number is how a metric that went 0 -> 0.1 reads as a catastrophe.
    pct_change: float | None
    #: True when the change moved the right way for this metric, None when
    #: there is nothing to compare or the value did not move.
    improved: bool | None


@dataclass(frozen=True)
class VersionComparison:
    """Whether a skill edit actually helped, decided from run outcomes.

    Deterministic on purpose. The model proposes the edit, so asking it to
    grade its own edit reintroduces exactly the unverified self-report the
    rest of this harness exists to eliminate. Every field here is computed
    from recorded runs.
    """

    task_id: str
    before_version: int
    after_version: int
    before_runs: int
    after_runs: int
    #: "improved" | "regressed" | "mixed" | "inconclusive"
    verdict: str
    #: -5..+5, negative meaning the newer version is worse. A single number is
    #: lossy by construction; it exists to be scannable, and `deltas` carries
    #: the honest detail underneath it.
    score: int
    #: One plain sentence, built from the deltas -- never model-generated.
    headline: str
    deltas: tuple[MetricDelta, ...]


def _pct_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return (after - before) / abs(before) * 100.0


def _clean(runs: tuple[RunScore, ...]) -> tuple[RunScore, ...]:
    """Drop runs that failed for reasons a skill edit could not affect.

    `partial` is what the harness records for transient infrastructure
    failures. A six-day mail outage inside the comparison window would
    otherwise read as an edit that broke the skill.
    """
    return tuple(r for r in runs if r.status != "partial")


def _describe(deltas: tuple[MetricDelta, ...], verdict: str) -> str:
    """Build the human sentence from the numbers, with no model involved."""
    moved = [d for d in deltas if d.improved is not None and d.pct_change is not None]
    if verdict == "inconclusive":
        return "Not enough clean runs on both versions to judge yet."
    if not moved:
        return "No measurable change between the two versions."
    moved.sort(key=lambda d: abs(d.pct_change or 0), reverse=True)
    top = moved[0]
    direction = "improved" if top.improved else "regressed"
    lead = f"{top.label.capitalize()} {direction} {abs(top.pct_change or 0):.0f}%"
    rest = [d for d in moved[1:] if abs(d.pct_change or 0) >= 5]
    if rest:
        tail = ", ".join(
            f"{d.label} {'better' if d.improved else 'worse'} by {abs(d.pct_change or 0):.0f}%"
            for d in rest
        )
        return f"{lead}; {tail}."
    return f"{lead}."


def compare_versions(
    card: SkillScorecard, before: int | None = None, after: int | None = None,
) -> VersionComparison | None:
    """Compare two versions of a skill using the runs each one produced.

    Defaults to the two most recent versions present in the history, which is
    the question an approved edit raises: did the thing I just approved help?
    Returns None when the scorecard has fewer than two versions to compare.
    """
    slices = card.by_version
    versions = [v for v in slices if v > 0]
    if len(versions) < 2:
        return None
    versions.sort()
    before_v = before if before is not None else versions[-2]
    after_v = after if after is not None else versions[-1]
    if before_v not in slices or after_v not in slices:
        return None

    old = _clean(slices[before_v].run_scores)
    new = _clean(slices[after_v].run_scores)

    # Hold the model constant. A version window that straddles a model swap
    # measures the swap, not the edit — the cheaper model looks like a
    # brilliant edit and the pricier one like a broken skill. Runs recorded
    # before model tracking group under "", which is treated as its own model
    # precisely so a comparison across that boundary reports inconclusive
    # rather than a confident wrong number.
    if new:
        reference_model = Counter(r.model for r in new).most_common(1)[0][0]
        old = tuple(r for r in old if r.model == reference_model)
        new = tuple(r for r in new if r.model == reference_model)
    old_card = VersionSlice(version=before_v, run_scores=old)
    new_card = VersionSlice(version=after_v, run_scores=new)

    # Guard fires are only per-task once events carry attribution. Where they
    # do not, the figure includes whatever else the heartbeat was doing, and
    # scoring on it manufactures confident nonsense — three unrelated skills
    # "regressing" because one reflection tick looped on a fourth.
    guards_attributable = not any(
        r.unattributed_guards for r in old + new
    )

    deltas: list[MetricDelta] = []
    weighted = 0.0
    for name, weight in _SCORE_WEIGHTS.items():
        b = getattr(old_card, name)
        a = getattr(new_card, name)
        pct = _pct_change(b, a)
        improved: bool | None = None
        if b is not None and a is not None and a != b:
            improved = (a > b) if _HIGHER_IS_BETTER[name] else (a < b)
        deltas.append(MetricDelta(
            name=name, label=_METRIC_LABELS[name], before=b, after=a,
            pct_change=pct, improved=improved,
        ))
        if name == "avg_guard_fires" and not guards_attributable:
            # Reported for context, never scored.
            deltas[-1] = MetricDelta(
                name=name, label=_METRIC_LABELS[name], before=b, after=a,
                pct_change=None, improved=None,
            )
            continue
        if improved is not None and pct is not None:
            # Cap each metric's contribution so one wild percentage (a cost
            # that went 0.01 -> 0.03) cannot dominate the headline score.
            contribution = min(abs(pct) / 50.0, 1.0) * weight
            weighted += contribution if improved else -contribution

    # A refused reply outweighs every average: it means the new version
    # claimed work it had not done.
    new_blocks = sum(r.reply_blocks for r in new)
    if new_blocks:
        weighted = min(weighted, -float(_SCORE_WEIGHTS["compliance_rate"]))

    enough = len(old) >= _MIN_RUNS_FOR_VERDICT and len(new) >= _MIN_RUNS_FOR_VERDICT
    if not enough:
        verdict = "inconclusive"
    elif new_blocks:
        verdict = "regressed"
    else:
        directions = {d.improved for d in deltas if d.improved is not None}
        if not directions:
            verdict = "mixed"
        elif directions == {True}:
            verdict = "improved"
        elif directions == {False}:
            verdict = "regressed"
        else:
            verdict = "improved" if weighted > 0.5 else "regressed" if weighted < -0.5 else "mixed"

    score = 0 if verdict == "inconclusive" else max(-5, min(5, round(weighted)))
    return VersionComparison(
        task_id=card.task_id,
        before_version=before_v, after_version=after_v,
        before_runs=len(old), after_runs=len(new),
        verdict=verdict, score=score,
        headline=_describe(tuple(deltas), verdict),
        deltas=tuple(deltas),
    )


def _events_between(
    events: list[dict], start_ts: str | None, end_ts: str, task_id: str = "",
) -> list[dict]:
    """Events with ts in (start_ts, end_ts] — start_ts=None means
    "since the beginning of the log".

    A time window alone is not attribution. The heartbeat interleaves tasks,
    and a reflection tick looping on one skill once put 214 guard fires inside
    every other task's window, which reads as three unrelated skills all
    regressing at once. So when a task id is given, events carrying a
    different `task` are excluded.

    Events with no `task` at all predate that stamping. They are kept, because
    dropping them would silently rewrite every historical metric — but they
    are exactly the ones that cannot be trusted for per-task attribution, and
    `attributed_events` below reports how much of a window is affected.
    """
    out = []
    for e in events:
        ts = str(e.get("ts", ""))
        if not ts or ts > end_ts:
            continue
        if start_ts is not None and ts <= start_ts:
            continue
        owner = str(e.get("task") or "")
        if task_id and owner and owner != task_id:
            continue
        out.append(e)
    return out


def version_timeline(memory_root: Path, skill_name: str) -> tuple[tuple[str, int], ...]:
    """When each version of a skill went live, oldest first.

    `Skills.save` writes the new body and records that version's timestamp in
    the same call, so version N was the live body from its timestamp until the
    next version's. That makes the history reconstructable for runs recorded
    before version stamping existed.
    """
    from homunculus.skills import Skills

    try:
        versions = Skills(memory_root).versions(skill_name)
    except (OSError, json.JSONDecodeError):
        return ()
    out = [
        (str(v.get("timestamp") or ""), int(v.get("version") or 0))
        for v in versions
        if v.get("timestamp") and v.get("version")
    ]
    return tuple(sorted(out))


def infer_skill_version(timeline: tuple[tuple[str, int], ...], run_ts: str) -> int:
    """Which version was live when a run happened. 0 when it predates them all.

    A run older than the first recorded version ran under a body that was never
    archived, so it belongs to no known version — 0, never version 1. Both
    timestamps are naive local ISO-8601, which compares correctly as text.
    """
    live = 0
    for ts, version in timeline:
        if ts <= run_ts:
            live = version
        else:
            break
    return live


def score_skill(
    task_id: str, contract: Contract, runs: list[dict], events: list[dict],
    timeline: tuple[tuple[str, int], ...] = (),
) -> SkillScorecard:
    """Score every run in a task's `last_runs` history against its
    skill's contract, windowing events between consecutive runs.

    `timeline` backfills the skill version for runs recorded before it was
    stamped, from the version history's own timestamps. Without it a scorecard
    would have to wait days to say anything, while the answer to "did any of
    these twelve edits help" is already sitting on disk.
    """
    # A dry run never reached the user, so it is evidence about the code, not
    # about the skill. Scoring it would let a rehearsal move a verdict.
    runs = [r for r in runs if not r.get("dry_run")]
    ordered = sorted(runs, key=lambda r: str(r.get("ts", "")))
    if timeline:
        ordered = [
            r if r.get("skill_version") else
            {**r, "skill_version": infer_skill_version(timeline, str(r.get("ts", "")))}
            for r in ordered
        ]
    scores = []
    prev_ts: str | None = None
    for run in ordered:
        end_ts = str(run.get("ts", ""))
        window = _events_between(events, prev_ts, end_ts, task_id)
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
        out[task["id"]] = score_skill(
            task["id"], contract, task.get("last_runs") or [], events,
            timeline=version_timeline(memory_root, skill_name),
        )
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
