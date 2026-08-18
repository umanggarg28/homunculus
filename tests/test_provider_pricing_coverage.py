"""Every model the fallback chain can route to must be priced.

A missing entry is not a missing number: `_pricing_for` falls back to
`_DEFAULT_PAID_PRICING_CENTS`, deliberately set at frontier rates so an
unknown paid model fails CLOSED on cost. That default is right for a model
someone swaps in at runtime via /use, and wrong for one this deployment
configures on purpose — it billed a cheap fallback at 25-65x its real price
and exhausted a day's ceiling in ninety seconds, on ~7 cents of real spend.

So the chain's own model ids are pinned here. The same model reached through
two providers is two ids (Cerebras answers as "gpt-oss-120b", OpenRouter as
"openai/gpt-oss-120b") and each must be listed in the spelling the provider
reports back.
"""

import importlib

import pytest


def _chain_model_ids(llm) -> set[str]:
    """Every model id in the configured chain, comma-specs expanded."""
    specs = [
        llm.MODEL,
        llm.MODEL_FALLBACK,
        llm.MODEL_FALLBACK_2,
        llm.MODEL_FALLBACK_3,
    ]
    ids: set[str] = set()
    for spec in specs:
        ids.update(llm._expand_model_spec(spec or ""))
    return {m for m in ids if m}


@pytest.fixture
def llm():
    import homunculus.llm as _llm
    return importlib.reload(_llm)


def test_every_configured_model_is_priced(llm):
    unpriced = {
        m for m in _chain_model_ids(llm)
        # A :free route costs nothing, so it needs no entry.
        if llm._pricing_for(m) is not None and m not in llm._MODEL_PRICING_CENTS
    }
    assert not unpriced, (
        f"models in the fallback chain with no pricing entry: {sorted(unpriced)}. "
        "They would be billed at _DEFAULT_PAID_PRICING_CENTS (frontier rates) "
        "and can exhaust the daily budget on trivial spend. Add real rates to "
        "_MODEL_PRICING_CENTS."
    )


def test_default_pricing_still_applies_to_an_unconfigured_model(llm):
    """The fail-closed default must stay in place for models we did NOT
    configure — a /use swap to something unlisted still gets counted."""
    assert llm._pricing_for("some-vendor/never-seen-model") == llm._DEFAULT_PAID_PRICING_CENTS


def test_free_routes_are_never_priced(llm):
    assert llm._pricing_for("openai/gpt-oss-120b:free") is None


def test_env_configured_fallback_is_caught(monkeypatch):
    """The check reads the live env, so a deployment that adds a fallback
    without pricing it fails here rather than in the budget."""
    monkeypatch.setenv("HOMUNCULUS_MODEL_FALLBACK_2", "brand-new/unpriced-model")
    import homunculus.llm as _llm
    reloaded = importlib.reload(_llm)
    try:
        unpriced = {
            m for m in _chain_model_ids(reloaded)
            if reloaded._pricing_for(m) is not None
            and m not in reloaded._MODEL_PRICING_CENTS
        }
        assert "brand-new/unpriced-model" in unpriced
    finally:
        monkeypatch.undo()
        importlib.reload(_llm)
