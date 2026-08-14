"""Trajectory eval scoring — pure functions over run history + events.

Two fixtures are pulled from this session's real incidents (not
invented): a clean post-swap quiz-coach state-machine run, and the
Jul 29-Aug 1 tool-registry outage shape (silent drops, zero
completions) — the scorer must read the outage window as bad, not
crash on an empty tool_trace or missing events.
"""

from __future__ import annotations

from pathlib import Path

from homunculus import evals


# ---- Contract loading --------------------------------------------------


def _write_skill(memory_root: Path, name: str, frontmatter: str) -> None:
    (memory_root / f"{name}.md").write_text(
        f"---\n{frontmatter}\n---\n\n# body\n", encoding="utf-8",
    )


def test_load_contract_states(tmp_path: Path):
    _write_skill(
        tmp_path, "skill_quiz_coach",
        "name: skill_quiz_coach\nstates:\n  - tool: quiz_pick\n  - tool: notify\n  - tool: complete_task\n",
    )
    contract = evals.load_contract(tmp_path, "skill_quiz_coach")
    assert contract.kind == "states"
    assert contract.states == ("quiz_pick", "notify", "complete_task")


def test_load_contract_requires_tools(tmp_path: Path):
    _write_skill(
        tmp_path, "skill_daily_brief",
        "name: skill_daily_brief\nrequires_tools: [get_weather, task_health_summary, news_headlines]\n",
    )
    contract = evals.load_contract(tmp_path, "skill_daily_brief")
    assert contract.kind == "requires_tools"
    assert contract.requires_tools == frozenset({"get_weather", "task_health_summary", "news_headlines"})


def test_load_contract_missing_skill_file(tmp_path: Path):
    contract = evals.load_contract(tmp_path, "skill_does_not_exist")
    assert contract.kind == "none"


# ---- score_run: LCS subsequence match for states skills -----------------


def test_states_contract_exact_match_complies():
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    run = {"ts": "t1", "status": "success", "tool_trace": "quiz_pick, notify, complete_task", "calls": 3}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is True
    assert score.expected_calls == 3


def test_states_contract_tolerates_extra_harness_calls():
    """Extra calls (load_tool, a read-cache hit) between pinned tools
    don't break the match — only order/presence of the pinned tools
    matters (the LCS-subsequence property agentevals names)."""
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    run = {"ts": "t1", "status": "success",
           "tool_trace": "load_tool, quiz_pick, get_current_time, notify, complete_task", "calls": 5}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is True


def test_states_contract_out_of_order_fails():
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    run = {"ts": "t1", "status": "partial", "tool_trace": "notify, quiz_pick, complete_task", "calls": 3}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is False


def test_states_contract_missing_tool_fails():
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    run = {"ts": "t1", "status": "partial", "tool_trace": "quiz_pick", "calls": 1}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is False


# ---- score_run: unordered coverage for requires_tools skills ------------


def test_requires_tools_contract_order_agnostic():
    contract = evals.Contract(requires_tools=frozenset({"get_weather", "news_headlines"}))
    run = {"ts": "t1", "status": "success",
           "tool_trace": "news_headlines, task_health_summary, get_weather, notify, complete_task", "calls": 5}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is True
    assert score.expected_calls is None  # no fixed length for free-form skills


def test_requires_tools_contract_missing_one_fails():
    contract = evals.Contract(requires_tools=frozenset({"get_weather", "news_headlines"}))
    run = {"ts": "t1", "status": "partial", "tool_trace": "get_weather, notify, complete_task", "calls": 3}
    score = evals.score_run(contract, run, events_window=[])
    assert score.contract_compliance is False


# ---- Violations and guard fires from the events window ------------------


def test_violations_counted_from_events_window():
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    events = [
        {"ts": "t0.5", "event": "required_tool_violation"},
        {"ts": "t0.6", "event": "wrong_forced_tool"},
        {"ts": "t0.7", "event": "output_guard", "text": "stuck loop: remember"},
        {"ts": "t0.8", "event": "output_guard", "text": "cache hit: list_tasks × 2"},
        {"ts": "t0.9", "event": "service_ping"},
    ]
    score = evals.score_run(contract, run, events_window=events)
    assert score.violations == 2
    assert score.guard_fires == 1  # the cache-hit doesn't count as a real guard fire


# ---- guard classification is structural, not prose-matching --------------
#
# `output_guard` is emitted by two different subsystems for six different
# reasons. Classifying them by substring on a display string means any reword
# of a log line silently changes a metric that feeds the model scorecard.


def test_guard_kinds_are_classified_by_tag_not_wording():
    """Every dispatch guard except the cache hit counts, whatever its text."""
    kinds = ["stuck_loop", "permission_denied", "args_corrected",
             "name_syntax_leak", "reply_blocked"]
    events = [{"event": "output_guard", "kind": k, "text": "reworded"} for k in kinds]
    events.append({"event": "output_guard", "kind": "cache_hit", "text": "reworded"})
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    score = evals.score_run(contract, run, events_window=events)
    assert score.guard_fires == len(kinds)


def test_rewording_a_log_line_does_not_move_the_metric():
    """The regression this classification exists to prevent: a cache-hit event
    whose text no longer says "cache hit" must still be excluded."""
    events = [{"event": "output_guard", "kind": "cache_hit",
               "text": "served from per-turn cache: list_tasks (2nd call)"}]
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    assert evals.score_run(contract, run, events_window=events).guard_fires == 0


def test_untagged_historical_events_keep_their_original_scoring():
    """Traces written before `kind` existed must not be rescored — the model
    head-to-head compares runs across that boundary."""
    events = [
        {"event": "output_guard", "text": "cache hit: list_tasks × 2"},
        {"event": "output_guard", "text": "stuck loop: read_file × 3"},
    ]
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    assert evals.score_run(contract, run, events_window=events).guard_fires == 1


def test_tag_wins_over_text_when_both_are_present():
    """A tagged event is never re-judged by its prose, even when the prose
    would have said otherwise."""
    events = [{"event": "output_guard", "kind": "stuck_loop",
               "text": "cache hit: this wording would fool the legacy path"}]
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    assert evals.score_run(contract, run, events_window=events).guard_fires == 1


# ---- score_skill: real-incident fixtures ---------------------------------


def test_clean_post_swap_quiz_coach_run_scores_well():
    """The real Aug 1 quiz-coach run after the DeepSeek swap: 2 LLM
    calls, 0 violations, clean state-machine compliance."""
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    runs = [{
        "ts": "2026-08-01T08:02:07",
        "status": "success",
        "tool_trace": "quiz_pick, notify, complete_task",
        "calls": 2,
        "cost_cents": 0.15,
    }]
    events = [
        {"ts": "2026-08-01T08:02:00", "event": "state_machine_step", "text": "state 1/3"},
        {"ts": "2026-08-01T08:02:03", "event": "state_machine_step", "text": "state 2/3"},
    ]
    card = evals.score_skill("quiz-coach", contract, runs, events)
    assert card.compliance_rate == 1.0
    assert card.avg_violations == 0.0


def test_outage_window_scores_as_bad_not_a_crash():
    """The Jul 29 tool-registry outage: three consecutive silent-drop
    partials with empty tool_trace and no completion. The scorer must
    read this as non-compliant, not raise on missing/empty fields."""
    contract = evals.Contract(states=("quiz_pick", "notify", "complete_task"))
    runs = [
        {"ts": "2026-07-29T14:30:21", "status": "partial", "tool_trace": "quiz_pick", "calls": 1},
        {"ts": "2026-07-29T14:41:55", "status": "partial", "tool_trace": "", "calls": 0},
        {"ts": "2026-07-29T14:52:14", "status": "partial", "tool_trace": "", "calls": 0},
    ]
    events: list[dict] = []
    card = evals.score_skill("quiz-coach", contract, runs, events)
    assert card.runs == 3
    assert card.compliance_rate == 0.0
    assert all(not r.contract_compliance for r in card.run_scores)


# ---- by_model: splitting a scorecard's history across a model swap ------


def test_score_run_reads_model_from_run_record():
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1,
           "model": "deepseek/deepseek-v4-flash-0731"}
    score = evals.score_run(contract, run, events_window=[])
    assert score.model == "deepseek/deepseek-v4-flash-0731"


def test_score_run_model_defaults_to_empty_string_for_legacy_runs():
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    score = evals.score_run(contract, run, events_window=[])
    assert score.model == ""


def test_by_model_splits_history_across_a_swap():
    contract = evals.Contract(states=("notify",))
    runs = (
        [{"ts": f"t{i:03d}", "status": "success", "tool_trace": "", "calls": 1,
          "model": "openai/gpt-oss-120b"} for i in range(3)]
        + [{"ts": f"t{i:03d}", "status": "success", "tool_trace": "notify", "calls": 1,
            "model": "deepseek/deepseek-v4-flash-0731"} for i in range(3, 8)]
    )
    card = evals.score_skill("x", contract, runs, events=[])
    by_model = card.by_model
    assert set(by_model) == {"openai/gpt-oss-120b", "deepseek/deepseek-v4-flash-0731"}
    assert by_model["openai/gpt-oss-120b"].runs == 3
    assert by_model["openai/gpt-oss-120b"].compliance_rate == 0.0
    assert by_model["deepseek/deepseek-v4-flash-0731"].runs == 5
    assert by_model["deepseek/deepseek-v4-flash-0731"].compliance_rate == 1.0


def test_by_model_groups_legacy_runs_under_empty_string():
    contract = evals.Contract(states=("notify",))
    runs = [{"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}]
    card = evals.score_skill("x", contract, runs, events=[])
    assert list(card.by_model) == [""]
    assert card.by_model[""].runs == 1


def test_by_model_empty_when_no_runs():
    card = evals.SkillScorecard(task_id="x", contract_kind="states", run_scores=())
    assert card.by_model == {}


def test_scorecard_empty_runs_reports_none_not_zero_division():
    card = evals.SkillScorecard(task_id="x", contract_kind="states", run_scores=())
    assert card.compliance_rate is None
    assert card.avg_violations is None
    assert card.trend == "insufficient_data"


def test_trend_needs_at_least_four_runs():
    contract = evals.Contract(states=("notify",))
    runs = [
        {"ts": f"t{i}", "status": "success", "tool_trace": "notify", "calls": 1}
        for i in range(3)
    ]
    card = evals.score_skill("x", contract, runs, events=[])
    assert card.trend == "insufficient_data"


def test_trend_detects_improvement():
    contract = evals.Contract(states=("notify",))
    # Zero-padded fixed-width timestamps, spaced 10 apart, so lexicographic
    # string comparison (what _events_between actually uses) sorts them
    # the same as their numeric value — no real datetimes needed.
    run_ts = [f"t{10 * (i + 1):03d}" for i in range(6)]  # t010, t020, ..., t060
    runs = [
        {"ts": ts, "status": "success", "tool_trace": "notify", "calls": 1}
        for ts in run_ts
    ]
    # 2 violations landing in each of the first 3 runs' windows (one tick
    # before that run's own ts), 0 in the last 3 — an improving trend.
    events = []
    for ts in run_ts[:3]:
        before = f"t{int(ts[1:]) - 1:03d}"
        events.append({"ts": before, "event": "required_tool_violation"})
        events.append({"ts": before, "event": "wrong_forced_tool"})
    card = evals.score_skill("x", contract, runs, events)
    assert card.trend == "improving"


# ---- score_all / load_tasks / load_events --------------------------------


def test_score_all_skips_tasks_without_a_skill(tmp_path: Path):
    tasks = [
        {"id": "reminder-task", "last_runs": []},  # no `skill` field
        {"id": "quiz-coach", "skill": "skill_quiz_coach", "last_runs": []},
    ]
    _write_skill(tmp_path, "skill_quiz_coach", "name: skill_quiz_coach\nstates:\n  - tool: notify\n")
    out = evals.score_all(tasks, events=[], memory_root=tmp_path)
    assert set(out) == {"quiz-coach"}


def test_load_tasks_handles_both_json_shapes(tmp_path: Path):
    import json

    p1 = tmp_path / "wrapped.json"
    p1.write_text(json.dumps({"tasks": [{"id": "a"}]}), encoding="utf-8")
    p2 = tmp_path / "bare.json"
    p2.write_text(json.dumps([{"id": "b"}]), encoding="utf-8")
    p3 = tmp_path / "missing.json"

    assert evals.load_tasks(p1) == [{"id": "a"}]
    assert evals.load_tasks(p2) == [{"id": "b"}]
    assert evals.load_tasks(p3) == []


def test_load_events_skips_malformed_lines(tmp_path: Path):
    p = tmp_path / "events.jsonl"
    p.write_text('{"ts": "t1", "event": "x"}\nnot json\n{"ts": "t2", "event": "y"}\n', encoding="utf-8")
    out = evals.load_events(p)
    assert len(out) == 2


def test_load_events_missing_file(tmp_path: Path):
    assert evals.load_events(tmp_path / "nope.jsonl") == []


# ---- refused replies are tracked apart from loop guards ------------------
#
# A blocked reply is a subset of guard_fires, carried separately because it is
# rare and severe: averaged in with hundreds of loop fires it would round to
# invisibility, which defeats the point of recording it at all.


def _score(events):
    contract = evals.Contract(states=("notify",))
    run = {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1}
    return evals.score_run(contract, run, events_window=events)


def test_reply_block_is_counted_separately_and_also_as_a_guard_fire():
    score = _score([
        {"event": "output_guard", "kind": "reply_blocked", "violations": "unsupported_claim"},
        {"event": "output_guard", "kind": "stuck_loop", "text": "stuck loop: read_file × 3"},
    ])
    assert score.reply_blocks == 1
    assert score.guard_fires == 2, "a refused reply is still a guard fire"


def test_loop_guards_are_not_counted_as_refused_replies():
    score = _score([
        {"event": "output_guard", "kind": "stuck_loop", "text": "x"},
        {"event": "output_guard", "kind": "cache_hit", "text": "y"},
        {"event": "output_guard", "kind": "permission_denied", "text": "z"},
    ])
    assert score.reply_blocks == 0


def test_untagged_reply_block_identified_by_its_violations_field():
    """Historical reply blocks carry `violations`, which no dispatch guard
    has ever written."""
    score = _score([{"event": "output_guard", "violations": "fabricated_link",
                     "preview": "Here is the report..."}])
    assert score.reply_blocks == 1


def test_scorecard_reports_refusals_as_a_total_not_an_average():
    """Dividing by run count would round a single occurrence toward zero."""
    scores = tuple(
        evals.RunScore(ts=f"t{i}", status="success", contract_kind="states",
                       contract_compliance=True, violations=0, guard_fires=1,
                       calls=1, expected_calls=1, cost_cents=0.0,
                       reply_blocks=(1 if i == 0 else 0))
        for i in range(20)
    )
    card = evals.SkillScorecard(task_id="t", contract_kind="states", run_scores=scores)
    assert card.reply_blocks == 1, "one refusal in 20 runs must still read as 1"


# ---- did the skill edit actually help? -----------------------------------
#
# The agent proposes the edit, so it cannot be the one to grade it. These pin
# that the verdict is computed from recorded runs and nothing else.


def _run(version, *, compliance=True, guards=1, cost=0.30, status="success", blocks=0):
    return evals.RunScore(
        ts="t", status=status, contract_kind="states",
        contract_compliance=compliance, violations=0, guard_fires=guards,
        calls=3, expected_calls=3, cost_cents=cost,
        reply_blocks=blocks, skill_version=version,
    )


def _card(runs):
    return evals.SkillScorecard(
        task_id="hn", contract_kind="states", run_scores=tuple(runs),
    )


def test_by_version_groups_runs_and_keeps_unstamped_separate():
    card = _card([_run(3), _run(3), _run(4), _run(0)])
    assert set(card.by_version) == {0, 3, 4}
    assert card.by_version[3].runs == 2


def test_comparison_needs_two_versions():
    assert evals.compare_versions(_card([_run(3), _run(3)])) is None


def test_verdict_is_inconclusive_below_the_run_floor():
    """Two runs a side is not evidence; saying so is the honest output."""
    c = evals.compare_versions(_card([_run(3), _run(3), _run(4), _run(4)]))
    assert c is not None and c.verdict == "inconclusive"
    assert c.score == 0
    assert "not enough" in c.headline.lower()


def test_a_clearly_better_version_reads_as_improved():
    old = [_run(3, compliance=False, guards=12, cost=0.40) for _ in range(4)]
    new = [_run(4, compliance=True, guards=2, cost=0.20) for _ in range(4)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    assert c.verdict == "improved"
    assert c.score > 0
    assert c.before_version == 3 and c.after_version == 4


def test_a_clearly_worse_version_reads_as_regressed():
    old = [_run(3, compliance=True, guards=2, cost=0.20) for _ in range(4)]
    new = [_run(4, compliance=False, guards=12, cost=0.40) for _ in range(4)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None and c.verdict == "regressed" and c.score < 0


def test_a_refused_reply_in_the_new_version_overrides_every_average():
    """Cheaper and fewer guard fires does not excuse claiming work it did not
    do — the one failure mode the whole harness exists to prevent."""
    old = [_run(3, guards=9, cost=0.40) for _ in range(4)]
    new = [_run(4, guards=1, cost=0.10) for _ in range(3)] + [_run(4, guards=1, cost=0.10, blocks=1)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None and c.verdict == "regressed"


def test_infra_failures_are_excluded_from_the_comparison():
    """A provider outage inside the window is not the edit's fault. `partial`
    is what the harness records for transient infrastructure failures."""
    old = [_run(3, guards=2) for _ in range(4)]
    new = [_run(4, guards=2) for _ in range(4)] + [
        _run(4, compliance=False, guards=40, status="partial") for _ in range(5)
    ]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    assert c.after_runs == 4, "the five outage runs must not count"
    assert c.verdict != "regressed"


def test_percent_change_from_zero_is_not_reported():
    """0 -> 0.1 is not an infinite regression; the percent is undefined and
    reporting it as a number is how a trivial change reads as a disaster."""
    old = [_run(3, guards=0) for _ in range(4)]
    new = [_run(4, guards=1) for _ in range(4)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    guard_delta = next(d for d in c.deltas if d.name == "avg_guard_fires")
    assert guard_delta.pct_change is None
    assert guard_delta.improved is False


def test_headline_names_the_biggest_mover_in_plain_words():
    old = [_run(3, guards=10, cost=0.30) for _ in range(4)]
    new = [_run(4, guards=1, cost=0.30) for _ in range(4)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    assert "guard fires per run" in c.headline.lower()
    assert "improved" in c.headline.lower()


# ---- reconstructing which version produced a historical run --------------


def test_version_inference_picks_the_body_live_at_the_time():
    timeline = (("2026-06-01T00:00:00", 1), ("2026-07-01T00:00:00", 2), ("2026-08-01T00:00:00", 3))
    assert evals.infer_skill_version(timeline, "2026-06-15T12:00:00") == 1
    assert evals.infer_skill_version(timeline, "2026-07-02T12:00:00") == 2
    assert evals.infer_skill_version(timeline, "2026-09-01T12:00:00") == 3


def test_runs_older_than_every_version_belong_to_none_of_them():
    """A run predating the first archive ran under a body never snapshotted.
    Attributing it to v1 would credit an edit with runs it never produced."""
    timeline = (("2026-06-01T00:00:00", 1),)
    assert evals.infer_skill_version(timeline, "2026-05-31T23:59:59") == 0


def test_an_explicit_stamp_is_never_overwritten_by_inference():
    contract = evals.Contract(states=("notify",))
    runs = [{"ts": "2026-07-15T00:00:00", "status": "success",
             "tool_trace": "notify", "calls": 1, "skill_version": 9}]
    timeline = (("2026-06-01T00:00:00", 1), ("2026-07-01T00:00:00", 2))
    card = evals.score_skill("t", contract, runs, [], timeline=timeline)
    assert card.run_scores[0].skill_version == 9


def test_empty_timeline_leaves_runs_unversioned():
    contract = evals.Contract(states=("notify",))
    runs = [{"ts": "2026-07-15T00:00:00", "status": "success", "tool_trace": "notify", "calls": 1}]
    card = evals.score_skill("t", contract, runs, [], timeline=())
    assert card.run_scores[0].skill_version == 0


def test_guard_fires_are_not_scored_when_they_cannot_be_attributed():
    """The regression this exists to prevent: one reflection tick looping on
    one skill put 214 guard fires inside every other task's time window, and
    three unrelated skills all read as 'regressed'."""
    old = [_run(3, guards=1) for _ in range(4)]
    new = [_run(4, guards=40) for _ in range(4)]
    # Mark the new runs' guard counts as coming from unattributed events.
    new = [
        evals.RunScore(**{**r.__dict__, "unattributed_guards": 40}) for r in new
    ]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    guard = next(d for d in c.deltas if d.name == "avg_guard_fires")
    assert guard.improved is None and guard.pct_change is None
    assert c.verdict != "regressed", "an unattributable metric must not drive a verdict"


def test_attributed_guard_fires_still_count():
    old = [_run(3, guards=10) for _ in range(4)]
    new = [_run(4, guards=1) for _ in range(4)]
    c = evals.compare_versions(_card(old + new))
    assert c is not None
    guard = next(d for d in c.deltas if d.name == "avg_guard_fires")
    assert guard.improved is True


def test_events_from_another_task_are_excluded_from_the_window():
    contract = evals.Contract(states=("notify",))
    runs = [{"ts": "2026-08-10T10:00:00", "status": "success", "tool_trace": "notify", "calls": 1}]
    events = [
        {"ts": "2026-08-10T09:59:00", "event": "output_guard", "kind": "stuck_loop",
         "task": "some-other-task", "text": "stuck loop: propose_skill"},
        {"ts": "2026-08-10T09:59:30", "event": "output_guard", "kind": "stuck_loop",
         "task": "mine", "text": "stuck loop: read_file"},
    ]
    card = evals.score_skill("mine", contract, runs, events)
    assert card.run_scores[0].guard_fires == 1, "only this task's fire counts"
    assert card.run_scores[0].unattributed_guards == 0


def test_a_comparison_across_a_model_swap_is_inconclusive():
    """A version window that straddles a model change measures the model, not
    the edit. Reporting a verdict there is worse than reporting nothing."""
    old = tuple(evals.RunScore(**{**_run(3, guards=2, cost=0.10).__dict__, "model": "old-model"})
                for _ in range(5))
    new = tuple(evals.RunScore(**{**_run(4, guards=2, cost=0.40).__dict__, "model": "new-model"})
                for _ in range(5))
    c = evals.compare_versions(_card(list(old) + list(new)))
    assert c is not None
    assert c.before_runs == 0, "no old runs share the new model"
    assert c.verdict == "inconclusive"


def test_same_model_on_both_sides_still_produces_a_verdict():
    old = tuple(evals.RunScore(**{**_run(3, guards=9, cost=0.40).__dict__, "model": "m"})
                for _ in range(4))
    new = tuple(evals.RunScore(**{**_run(4, guards=1, cost=0.10).__dict__, "model": "m"})
                for _ in range(4))
    c = evals.compare_versions(_card(list(old) + list(new)))
    assert c is not None and c.verdict == "improved"
