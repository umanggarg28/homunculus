"""Link-grounding verification — the (B) half of the anti-fabrication fix.

Source-agnostic: any external link in a delivery must have appeared in a tool
result this turn, else it's treated as fabricated. Generalises the prop-NNNN
artifact check (core.py) to all URLs, matching OpenClaw's "verify the URL
before using it" and Hermes's "verify every citation".
"""

from __future__ import annotations

import core


def test_extract_urls_normalizes_trailing_punctuation():
    urls = core._extract_urls("See https://news.example/1, and https://news.example/2.")
    assert urls == {"https://news.example/1", "https://news.example/2"}


def test_markdown_link_extracted():
    urls = core._extract_urls("- [Title](https://news.example/x)")
    assert "https://news.example/x" in urls


def test_unverified_when_not_in_tool_results():
    grounded = {"https://news.example/real"}
    bad = core._unverified_urls(
        "Top story: https://news.example/fabricated", grounded
    )
    assert bad == {"https://news.example/fabricated"}


def test_verified_when_link_came_from_tool_result():
    grounded = {"https://news.example/real"}
    bad = core._unverified_urls("Read https://news.example/real today", grounded)
    assert bad == set()


def test_allowlisted_hosts_pass_without_fetch():
    # The app's own surfaces don't need a fetch to be cited.
    bad = core._unverified_urls("Open http://localhost:8000/overview", set())
    assert bad == set()


def test_no_urls_is_clean():
    assert core._unverified_urls("Morning, Umang. Nothing scheduled today.", set()) == set()


def test_case_insensitive_match():
    grounded = {"https://news.example/a"}
    assert core._unverified_urls("HTTPS://News.Example/A", grounded) == set()
