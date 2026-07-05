"""Career wiki + job-posting tools (apply-assist checkpoint 1).

The contract: the career repo is read live from its mount (single
source of truth — no memory copy to drift), sentinels instead of
exceptions when it isn't mounted, ATS URLs resolve against public JSON
APIs with a digested text result, and unknown platforms degrade to a
plain fetch with an explicit "no form schema" marker.
"""

from tests.conftest import load_real_tool_submodule

career = load_real_tool_submodule("career")


def _wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_CAREER_DIR", str(tmp_path))
    (tmp_path / "CAREER-CONTEXT.md").write_text(
        "# Career Context\n\n## Personal\nName: Umang Garg\n\n"
        "## Visa\nH1-B cap-exempt, 3 of 6 years used.\n",
        encoding="utf-8",
    )
    (tmp_path / "cv.md").write_text(
        "# CV\n\n## Work Experience\nPictorLabs.ai — Full Stack Developer 2\n",
        encoding="utf-8",
    )


def test_career_context_reads_both_files(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    out = career.career_context()
    assert "H1-B cap-exempt" in out
    assert "PictorLabs.ai" in out
    assert "[CAREER-CONTEXT.md]" in out and "[cv.md]" in out


def test_career_context_section_filter(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    out = career.career_context("visa")
    assert "H1-B cap-exempt" in out
    assert "PictorLabs" not in out


def test_section_filter_matches_body_not_just_heading(tmp_path, monkeypatch):
    """Live regression 2026-07-05: 'visa' lives inside '## The
    Situation' in the real wiki — a heading-only filter found nothing."""
    monkeypatch.setenv("HOMUNCULUS_CAREER_DIR", str(tmp_path))
    (tmp_path / "CAREER-CONTEXT.md").write_text(
        "## The Situation\nVisa: H1-B cap-exempt.\n\n## Other\nUnrelated.\n",
        encoding="utf-8",
    )
    out = career.career_context("visa")
    assert "H1-B cap-exempt" in out
    assert "Unrelated" not in out


def test_career_context_unmounted_is_sentinel(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_CAREER_DIR", str(tmp_path / "nope"))
    out = career.career_context()
    assert out.startswith(career.CONTEXT_UNAVAILABLE)


def test_unknown_section_names_the_fix(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    out = career.career_context("nonexistent")
    assert "No section matching" in out


def test_greenhouse_url_digest(monkeypatch):
    captured = {}

    def fake_get(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return {
            "title": "Senior AI Engineer",
            "location": {"name": "Remote - US"},
            "content": "&lt;p&gt;Build agents.&lt;/p&gt;",
            "questions": [
                {"label": "Resume", "required": True, "fields": [{"type": "input_file"}]},
                {"label": "Why us?", "required": False, "fields": [{"type": "textarea"}]},
            ],
        }

    monkeypatch.setattr(career, "_get_json", fake_get)
    out = career.job_posting("https://boards.greenhouse.io/acme/jobs/4012345")
    assert "acme/jobs/4012345" in captured["url"]
    assert captured["params"] == {"questions": "true"}
    assert "[platform: greenhouse]" in out
    assert "Senior AI Engineer" in out
    assert "Resume [input_file · required]" in out
    assert "Why us? [textarea · optional]" in out
    assert "Build agents." in out  # html-entity-unescaped, tags stripped


def test_lever_url_digest(monkeypatch):
    def fake_get(url, params=None):
        assert "api.lever.co/v0/postings/acme/" in url
        return {
            "text": "Staff Engineer",
            "categories": {"location": "NYC", "team": "Platform", "commitment": "Full-time"},
            "applyUrl": "https://jobs.lever.co/acme/x/apply",
            "description": "<b>Do platform things.</b>",
            "lists": [{"text": "Requirements", "content": "<li>8 years</li>"}],
        }

    monkeypatch.setattr(career, "_get_json", fake_get)
    out = career.job_posting(
        "https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc"
    )
    assert "[platform: lever]" in out
    assert "Staff Engineer" in out
    assert "Do platform things." in out
    assert "8 years" in out


def test_api_failure_is_sentinel(monkeypatch):
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: None)
    out = career.job_posting("https://boards.greenhouse.io/acme/jobs/1")
    assert out.startswith(career.POSTING_UNAVAILABLE)


def test_unknown_platform_falls_back_to_fetch(monkeypatch):
    from homunculus.tools import web

    monkeypatch.setattr(web, "web_fetch", lambda url: "SOME JD TEXT")
    out = career.job_posting("https://example.com/careers/123")
    assert "no form schema" in out
    assert "SOME JD TEXT" in out


# ── application plans (CP2) ─────────────────────────────────────────

def _plan_env(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    (tmp_path / "CAREER-CONTEXT.md").write_text(
        "## Personal\n\n"
        "| | |\n|--|--|\n"
        "| **Name** | Umang Garg |\n"
        "| **Email** | umanggarg28@gmail.com |\n"
        "| **Phone** | +91 9717182251 |\n"
        "| **LinkedIn** | linkedin.com/in/umanggarg28 |\n",
        encoding="utf-8",
    )
    (tmp_path / "resume").mkdir()
    (tmp_path / "resume" / "UmangGarg-Resume.pdf").write_bytes(b"%PDF-1.4 fake")
    from homunculus.tools import _helpers

    monkeypatch.setattr(_helpers, "WORKSPACE_ROOT", tmp_path / "ws")


_GH_FIXTURE = {
    "title": "Senior AI Engineer",
    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/77",
    "location": {"name": "Remote"},
    "content": "JD",
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"type": "input_text"}]},
        {"label": "Email", "required": True, "fields": [{"type": "input_text"}]},
        {"label": "Resume/CV", "required": True, "fields": [{"type": "input_file"}]},
        {"label": "Why us?", "required": True, "fields": [{"type": "textarea"}]},
        {"label": "Earliest start date", "required": False, "fields": [{"type": "input_text"}]},
        {
            "label": "Open to office 2 days/week?",
            "required": True,
            "fields": [{"type": "multi_value_single_select",
                        "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}],
        },
    ],
}


def test_prepare_application_fills_facts_never_guesses(tmp_path, monkeypatch):
    _plan_env(tmp_path, monkeypatch)
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: dict(_GH_FIXTURE))
    out = career.prepare_application("https://boards.greenhouse.io/acme/jobs/77")
    assert "acme-77" in out
    assert "Why us?" in out  # listed as needing a draft

    import json
    plan = json.loads((tmp_path / "ws" / "applications" / "acme-77.json").read_text())
    by_label = {f["label"]: f for f in plan["fields"]}
    assert by_label["First Name"]["value"] == "Umang"
    assert by_label["First Name"]["source"] == "wiki"
    assert by_label["Email"]["value"] == "umanggarg28@gmail.com"
    assert by_label["Resume/CV"]["value"].endswith("UmangGarg-Resume.pdf")
    assert by_label["Why us?"]["value"] is None             # model's job, later
    assert by_label["Open to office 2 days/week?"]["value"] is None  # model may pre-choose, human confirms
    assert by_label["Open to office 2 days/week?"]["options"] == ["Yes", "No"]
    # short texts and selects are listed for drafting, options shown
    assert "Earliest start date" in out
    assert "choose ONE of: Yes | No" in out


def test_draft_answer_round_trip(tmp_path, monkeypatch):
    _plan_env(tmp_path, monkeypatch)
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: dict(_GH_FIXTURE))
    career.prepare_application("https://boards.greenhouse.io/acme/jobs/77")

    out = career.draft_answer("acme-77", "why us", "Because agents.")
    assert "Saved" in out
    import json
    plan = json.loads((tmp_path / "ws" / "applications" / "acme-77.json").read_text())
    q = next(f for f in plan["fields"] if f["label"] == "Why us?")
    assert q["value"] == "Because agents."
    assert q["source"] == "model"


def test_draft_answer_refuses_empty_and_unknown(tmp_path, monkeypatch):
    _plan_env(tmp_path, monkeypatch)
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: dict(_GH_FIXTURE))
    career.prepare_application("https://boards.greenhouse.io/acme/jobs/77")
    assert career.draft_answer("acme-77", "why us", "  ").startswith("ERROR")
    assert career.draft_answer("acme-77", "zzz", "text").startswith("ERROR")
    assert career.draft_answer("nope-1", "why us", "text").startswith("ERROR")


def test_prepare_application_non_greenhouse_says_so(monkeypatch):
    out = career.prepare_application("https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc")
    assert "Greenhouse URLs only" in out


def test_draft_answer_select_requires_exact_option(tmp_path, monkeypatch):
    _plan_env(tmp_path, monkeypatch)
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: dict(_GH_FIXTURE))
    career.prepare_application("https://boards.greenhouse.io/acme/jobs/77")

    out = career.draft_answer("acme-77", "office 2 days", "Absolutely yes")
    assert out.startswith("ERROR") and "EXACTLY one of" in out

    out = career.draft_answer("acme-77", "office 2 days", "no")
    assert "Saved" in out
    import json
    plan = json.loads((tmp_path / "ws" / "applications" / "acme-77.json").read_text())
    q = next(f for f in plan["fields"] if f["label"] == "Open to office 2 days/week?")
    assert q["value"] == "No"  # canonical option casing, not the model's


def test_visa_and_eeo_questions_are_human_only(tmp_path, monkeypatch):
    """Live failure 2026-07-06: the wiki says 'H1-B transfer, no
    lottery' and the model rounded it to 'No sponsorship required' —
    false on a real form. Legal/EEO questions never enter drafting."""
    _plan_env(tmp_path, monkeypatch)
    fixture = dict(_GH_FIXTURE)
    fixture["questions"] = list(fixture["questions"]) + [
        {"label": "Will you require visa sponsorship?", "required": True,
         "fields": [{"type": "multi_value_single_select",
                     "values": [{"label": "Yes"}, {"label": "No"}]}]},
        {"label": "Voluntary self-identification of gender", "required": False,
         "fields": [{"type": "multi_value_single_select",
                     "values": [{"label": "Male"}, {"label": "Female"}]}]},
    ]
    monkeypatch.setattr(career, "_get_json", lambda url, params=None: fixture)
    out = career.prepare_application("https://boards.greenhouse.io/acme/jobs/77")
    assert "Reserved for the user" in out
    assert "visa sponsorship" in out.lower()
    assert career.draft_answer("acme-77", "visa sponsorship", "No").startswith("ERROR")
