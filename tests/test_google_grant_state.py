"""A revoked Google grant should say so, once, to the person who can fix it.

`invalid_grant` from the token endpoint is permanent until a human re-consents
— the same shape as a 404 from a withdrawn model slug. Treated as a generic
failure it produced a daily "Google account not connected" notice that read
like a transient glitch, so nothing was ever done about it and the notice
arrived again the next morning.

The model's view is unchanged: it sees the sentinel and omits the section
either way, which is correct — the remedy is not its business. The change is
for the two audiences who can act: the operator reading `doctor`, and the
person receiving an outage notice.
"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import load_real_tool_submodule

google_auth = load_real_tool_submodule("google_auth")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(google_auth, "_LAST_FAILURE", {}, raising=False)


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x/token"))


def test_invalid_grant_is_classified_as_revoked():
    google_auth._note_grant_state(_resp(400, {"error": "invalid_grant"}))
    assert google_auth.grant_failure_reason() == "revoked"


def test_other_400s_are_not_called_revoked():
    """Only invalid_grant means 'nothing will heal on its own'."""
    google_auth._note_grant_state(_resp(400, {"error": "invalid_client"}))
    assert google_auth.grant_failure_reason() == "invalid_client"
    assert google_auth.unavailable_suffix() == ""


def test_a_malformed_body_does_not_raise():
    google_auth._note_grant_state(
        httpx.Response(400, text="<html>gateway</html>",
                       request=httpx.Request("POST", "https://x/token"))
    )
    assert google_auth.grant_failure_reason() == "bad_request"


def test_the_remedy_is_appended_only_when_revoked():
    assert google_auth.unavailable_suffix() == ""
    google_auth._note_grant_state(_resp(400, {"error": "invalid_grant"}))
    suffix = google_auth.unavailable_suffix()
    assert "scripts/google_auth.py" in suffix
    assert "no retry will recover it" in suffix


def test_the_sentinel_prefix_is_never_disturbed():
    """Skills and all three guards match on the token at the START of the
    result. Appending a cause must not move it."""
    from homunculus.sentinels import GMAIL_UNAVAILABLE, starts_with_sentinel
    UNAVAILABLE = load_real_tool_submodule("google_gmail").UNAVAILABLE

    google_auth._note_grant_state(_resp(400, {"error": "invalid_grant"}))
    message = UNAVAILABLE + google_auth.unavailable_suffix()
    assert message.startswith(GMAIL_UNAVAILABLE)
    assert starts_with_sentinel(message)


def test_doctor_reports_a_revoked_grant():
    """`load_real_tool_submodule` registers the module under its real name, so
    doctor's own import resolves to the same object this test mutates."""
    from homunculus import doctor

    assert doctor.audit_google_grant() == []
    google_auth._note_grant_state(_resp(400, {"error": "invalid_grant"}))
    findings = doctor.audit_google_grant()
    assert len(findings) == 1
    assert "scripts/google_auth.py" in findings[0].detail
