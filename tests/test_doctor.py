"""Startup posture audit.

Write-time validation only protects what is written after the rule exists.
These pin the part that re-examines what is already stored — and pin that it
stays advisory, because an audit that can stop the agent booting is one an
operator eventually deletes.
"""

from __future__ import annotations

import logging

from homunculus.doctor import Finding, audit_task_criteria, run_startup_audit


def _task(tid, criteria, *, skill=None, status="active"):
    t = {"id": tid, "success_criteria": criteria, "status": status}
    if skill:
        t["skill"] = skill
    return t


def test_weak_criteria_with_no_data_source_is_reported():
    findings = audit_task_criteria([_task("t1", [{"type": "notify_called"}])])
    assert len(findings) == 1
    assert "no data source" in findings[0].detail


def test_weak_criteria_with_a_data_source_notes_the_gate():
    """Still reported — but as defended, because every_required_source_failed
    gates it independently of the criteria."""
    findings = audit_task_criteria([
        _task("t2", [{"type": "notify_called"}], skill="skill_watch")
    ])
    assert len(findings) == 1
    assert "data-source gate" in findings[0].detail
    assert "skill_watch" in findings[0].detail


def test_adequate_criteria_are_silent():
    findings = audit_task_criteria([
        _task("t3", [{"type": "notify_called"},
                     {"type": "notify_min_chars", "n": 250},
                     {"type": "notify_contains", "text": "Morning"}])
    ])
    assert findings == []


def test_tasks_without_criteria_are_not_the_audit_s_business():
    """A task that declares nothing is a different concern; this check only
    judges criteria that exist."""
    assert audit_task_criteria([_task("t4", [])]) == []


def test_finished_tasks_are_ignored():
    weak = [{"type": "notify_called"}]
    assert audit_task_criteria([_task("t5", weak, status="completed")]) == []
    assert audit_task_criteria([_task("t6", weak, status="cancelled")]) == []


def test_audit_never_raises_on_malformed_tasks():
    """Stored state is not guaranteed well-formed; the audit must survive it."""
    for junk in ([{}], [{"id": None, "success_criteria": "not-a-list"}], [{"success_criteria": [None]}]):
        run_startup_audit(junk)  # must not raise


def test_a_broken_check_does_not_take_down_startup(monkeypatch, caplog):
    import homunculus.doctor as doctor

    def explode(_tasks):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "audit_task_criteria", explode)
    # Rebind the tuple the runner iterates by patching the module attribute
    # the function resolves at call time.
    with caplog.at_level(logging.WARNING):
        out = run_startup_audit([_task("t7", [{"type": "notify_called"}])])
    assert isinstance(out, list)


def test_findings_render_readably():
    f = Finding("task_criteria", "morning-brief", "criteria are weak")
    assert str(f) == "[task_criteria] morning-brief: criteria are weak"


def test_findings_are_logged_as_warnings(caplog):
    with caplog.at_level(logging.WARNING):
        run_startup_audit([_task("t8", [{"type": "notify_called"}])])
    assert any("doctor" in r.message for r in caplog.records)


def test_clean_config_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        out = run_startup_audit([
            _task("t9", [{"type": "notify_called"},
                         {"type": "notify_min_chars", "n": 300}])
        ])
    assert out == []
    assert not [r for r in caplog.records if "doctor" in r.message]


# --- a marker only counts when a failed run couldn't produce it ------------


def test_marker_repeating_the_task_title_does_not_count():
    """The real case. "📅 Event watch — Email not connected" satisfies
    notify_contains: "Event watch" exactly as well as a real delivery, because
    the agent writes that header either way."""
    findings = audit_task_criteria([{
        "id": "email-event-watch", "title": "Email event watch", "status": "active",
        "skill": "skill_email_event_reminders",
        "success_criteria": [{"type": "notify_called"},
                             {"type": "notify_min_chars", "n": 60},
                             {"type": "notify_contains", "text": "Event watch"}],
    }])
    assert len(findings) == 1


def test_marker_drawn_from_fetched_data_does_count():
    """"Totals:" only appears once the profile call returned numbers, so a
    short length floor is fine alongside it."""
    findings = audit_task_criteria([{
        "id": "github-health", "title": "GitHub health", "status": "active",
        "skill": "skill_github_health",
        "success_criteria": [{"type": "notify_called"},
                             {"type": "notify_min_chars", "n": 90},
                             {"type": "notify_contains", "text": "Totals:"}],
    }])
    assert findings == []


def test_structural_proof_counts_regardless_of_length():
    for proof in ("notify_links_grounded", "notify_has_code", "notify_unique"):
        findings = audit_task_criteria([{
            "id": "t", "title": "T", "status": "active",
            "success_criteria": [{"type": "notify_called"}, {"type": proof}],
        }])
        assert findings == [], proof


# ---- a dependency nothing declared is a dependency nothing gates ---------
#
# every_required_source_failed returns immediately when a skill declares no
# required sources. A skill that calls one tool on every successful run and
# declares nothing therefore has no source gate at all — the same shape as the
# six days of "Email not connected" recorded as six successful deliveries.


def _run(trace, status="success", **extra):
    return {"ts": "t", "status": status, "tool_trace": trace, **extra}


def _skilled(tid, skill, runs):
    return {"id": tid, "skill": skill, "status": "active", "last_runs": runs}


def test_a_source_used_every_run_but_undeclared_is_reported(tmp_path, monkeypatch):
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("github-health", "skill_github_health",
                    [_run("github_profile, notify, complete_task")] * 4)
    findings = doctor.audit_undeclared_sources([task], tmp_path)
    assert len(findings) == 1
    assert "github_profile" in findings[0].detail
    assert findings[0].check == "undeclared_source"


def test_a_declared_source_is_not_reported(tmp_path, monkeypatch):
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools",
                        lambda r, s: ["github_profile"])
    task = _skilled("github-health", "skill_github_health",
                    [_run("github_profile, notify")] * 4)
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_harness_tools_are_never_treated_as_data_sources(tmp_path, monkeypatch):
    """notify and complete_task appear in every run of every skill. Declaring
    them would say nothing about where the content came from."""
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("t", "skill_x", [_run("notify, complete_task, load_tool")] * 5)
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_a_tool_used_in_only_some_runs_is_not_a_dependency(tmp_path, monkeypatch):
    """Intersection, not frequency: a tool the skill sometimes reaches for is
    not something to gate a delivery on."""
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("t", "skill_x", [
        _run("quiz_pick, notify"), _run("quiz_pick, notify"), _run("notify"),
    ])
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_too_few_runs_is_coincidence_not_a_pattern(tmp_path, monkeypatch):
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("t", "skill_x", [_run("quiz_pick, notify")] * 2)
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_rehearsals_and_failures_do_not_establish_a_dependency(tmp_path, monkeypatch):
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("t", "skill_x", [
        _run("quiz_pick, notify", dry_run=True),
        _run("quiz_pick, notify", status="failure"),
        _run("quiz_pick, notify"),
    ])
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_repeat_counts_in_a_trace_are_parsed(tmp_path, monkeypatch):
    """A trace records repeats as 'tool ×4'; the name is still the tool."""
    import homunculus.doctor as doctor
    monkeypatch.setattr("homunculus.skills.load_skill_requires_tools", lambda r, s: [])
    task = _skilled("t", "skill_x", [_run("github_profile ×4, notify")] * 3)
    findings = doctor.audit_undeclared_sources([task], tmp_path)
    assert len(findings) == 1 and "github_profile" in findings[0].detail


def test_tasks_without_a_skill_are_skipped(tmp_path):
    import homunculus.doctor as doctor
    task = {"id": "t", "status": "active", "last_runs": [_run("x, notify")] * 4}
    assert doctor.audit_undeclared_sources([task], tmp_path) == []


def test_no_memory_root_means_no_findings_rather_than_a_crash(tmp_path):
    import homunculus.doctor as doctor
    task = _skilled("t", "skill_x", [_run("quiz_pick, notify")] * 4)
    assert doctor.audit_undeclared_sources([task], None) == []


def test_both_checks_run_and_one_failing_does_not_hide_the_other(monkeypatch, caplog):
    """The isolation contract, now that there is more than one check."""
    import homunculus.doctor as doctor
    monkeypatch.setattr(doctor, "audit_task_criteria",
                        lambda _t: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(doctor, "audit_undeclared_sources",
                        lambda _t, _r: [doctor.Finding("undeclared_source", "t", "d")])
    with caplog.at_level(logging.WARNING):
        out = doctor.run_startup_audit([], None)
    assert len(out) == 1, "a broken check must not suppress a working one"
