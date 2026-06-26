"""One taxonomy for classifying a run failure: platform/transient vs genuine.

A self-improving agent must only *learn* from failures it can actually fix. An
infrastructure failure — the LLM providers cooled, the network blipped, the host
disk filled (`OSError: [Errno 5]`) — has no skill fix; feeding it into the daily
reflection's "diagnose the skill and propose an edit" loop makes a weak model
thrash, hunting for a defect that isn't there (it once emitted a fabricated
skill-edit notice doing exactly this).

This is the standard pattern across mature systems: classify the failure, then
route it. Temporal splits `nonRetryable` (application) from retryable (platform);
LangGraph's `RetryPolicy.retry_on` retries transient classes (`ConnectionError`,
5xx, I/O) but not permanent ones (`ValueError` = a real bug); Reflexion reflects
only on genuine task failures. Here the split feeds the *learning* channel: the
reflection feed (`heartbeat._format_recent_deliveries`) consumes only genuine
failures. The *retry* channel (`heartbeat._is_transient_network_error`, an
exception-level check) is the sibling on the same idea.

Kept as deterministic substring markers — no model call — so it is cheap and
testable. Markers are intentionally tight: mis-labelling a genuine skill failure
as infrastructure would hide a real defect from reflection.
"""

from __future__ import annotations

# Unambiguous signatures of a platform/transient failure in a run's result text.
_TRANSIENT_MARKERS: tuple[str, ...] = (
    # Network blips (mirror the string checks in _is_transient_network_error).
    "connection refused", "network is unreachable", "name resolution",
    "temporary failure in name resolution", "connection reset",
    "connecterror", "connecttimeout", "readtimeout",
    "connect timeout", "read timeout",
    # Provider / model availability — the fallback chain cooled or rate-limited.
    "all providers exhausted", "provider_exhaustion", "provider exhaustion",
    "rate limit", "too many requests", "429",
    "service unavailable", "503", "502 bad gateway", "504 gateway timeout",
    # Platform / host I/O — the disk-full class.
    "input/output error", "[errno 5]", "errno 5",
    "no space left", "[errno 28]", "errno 28",
)


def is_transient_failure(text: str | None) -> bool:
    """True if ``text`` (a failed run's result/error) is a platform/transient
    failure — infrastructure, not a skill defect. Such failures are retried and
    alerted, but never fed to the skill-learning loop."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in _TRANSIENT_MARKERS)
