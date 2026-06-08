"""Required-tool-choice modes pin OpenRouter to providers that enforce.

After PR #122/#123 shipped, the live refinement run died with
text-only responses despite tool_choice='required' in the payload.
Root cause: OpenRouter round-robins the same model id across multiple
inference providers (Novita, DeepInfra, DekaLLM, Google). Some honor
tool_choice on the wire; others let the model bail to text.

`provider.require_parameters: true` tells OpenRouter to only route to
providers that support every request param — which transitively means
they enforce tool_choice when we ask. Verified with a direct curl
against DeepInfra (the canonical compliant provider).
"""

from __future__ import annotations

from unittest.mock import patch

import core


# ---- helper: _apply_provider_constraints ------------------------------


def test_apply_provider_constraints_writes_to_payload_for_openrouter() -> None:
    payload: dict = {"model": "x"}
    core._apply_provider_constraints(
        payload, "https://openrouter.ai/api/v1/chat/completions",
        {"require_parameters": True},
    )
    assert payload["provider"] == {"require_parameters": True}


def test_apply_provider_constraints_skips_non_openrouter() -> None:
    """Gemini, Groq, Cerebras don't have multi-provider routing; the
    `provider` field would be ignored or rejected. Only apply for
    OpenRouter URLs."""
    for url in (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://api.cerebras.ai/v1/chat/completions",
    ):
        payload: dict = {"model": "x"}
        core._apply_provider_constraints(
            payload, url, {"require_parameters": True},
        )
        assert "provider" not in payload, f"non-openrouter URL {url} got provider field"


def test_apply_provider_constraints_noop_when_none() -> None:
    payload: dict = {"model": "x"}
    core._apply_provider_constraints(
        payload, "https://openrouter.ai/api/v1/chat/completions", None,
    )
    assert "provider" not in payload


def test_apply_provider_constraints_copies_dict() -> None:
    """Defensive: caller's dict should not be aliased into the payload
    (so a later caller mutation can't change the in-flight request)."""
    constraints = {"require_parameters": True}
    payload: dict = {}
    core._apply_provider_constraints(
        payload, "https://openrouter.ai/api/v1/chat/completions", constraints,
    )
    constraints["something_else"] = "added later"
    assert payload["provider"] == {"require_parameters": True}


# ---- end-to-end: source dispatch sets the right constraints ----------


def test_all_sources_send_no_provider_constraints_today() -> None:
    """Historical: PR #124 set require_parameters=True for required-mode
    sources. Real-world testing showed it dropped the OpenRouter provider
    pool to zero ('No endpoints found that can handle the requested
    parameters'). We now rely on PR #125's defense-in-depth detector to
    catch tool_choice violations at the harness layer instead of
    enforcing them via strict routing.

    This test pins the current behavior: NO source sends provider
    constraints. The plumbing stays in place for future narrower hints
    (e.g. provider.order=[preferred,...] without require_parameters)."""
    agent = core.Agent(memory=None)

    def fake_for(source: str) -> tuple[list[dict | None], callable]:
        seen: list[dict | None] = []
        def fake(messages, tool_schemas, model=None, tool_choice="auto",
                 reasoning_effort="low", provider_constraints=None):
            seen.append(provider_constraints)
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": '{"task_id": "x", "result": "done"}',
                    },
                }],
            }
        return seen, fake

    for source in ("web", "heartbeat", "refinement"):
        seen, fake = fake_for(source)
        with patch.object(core, "call_llm", side_effect=fake):
            list(agent._run_loop("kick", streaming=False, source=source))
        assert seen, f"source={source}: expected call_llm to fire"
        assert all(c is None for c in seen), (
            f"source={source} must send no provider constraints, got: {seen}"
        )


# ---- non-breaking API ------------------------------------------------


def test_call_llm_default_provider_constraints_is_none() -> None:
    import inspect
    sig = inspect.signature(core.call_llm)
    assert sig.parameters["provider_constraints"].default is None


def test_call_llm_stream_default_provider_constraints_is_none() -> None:
    import inspect
    sig = inspect.signature(core.call_llm_stream)
    assert sig.parameters["provider_constraints"].default is None
