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
