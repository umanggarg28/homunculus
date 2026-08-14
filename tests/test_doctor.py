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
