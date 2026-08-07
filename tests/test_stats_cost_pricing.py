"""stats.model_cost_cents must agree with the budget enforcer's pricing.

Regression: stats.py carried its own pricing table, separate from
llm.py's. The tables drifted — the primary model (openai/gpt-oss-120b)
was missing from stats' copy — so every UI surface (sidebar budget,
Overview spend, per-run trace cost, week_in_review) showed ¢0.0 while
the budget enforcer accrued real spend against the daily ceiling.
model_cost_cents now delegates to llm._pricing_for: one pricing source.
"""

from homunculus.llm import _pricing_for
from homunculus.stats import model_cost_cents


def test_primary_model_is_costed_not_zero():
    cost = model_cost_cents("openai/gpt-oss-120b", 1_000_000, 100_000, 0)
    assert cost > 0, "the primary paid model must never appear free in the UI"
    # 1M uncached input at 3.7c + 100k output at 17c/1M = 3.7 + 1.7 cents.
    assert abs(cost - 5.4) < 0.01


def test_free_models_cost_zero():
    assert model_cost_cents("meta-llama/llama-3.3-70b-instruct:free", 5_000_000, 1_000_000, 0) == 0.0
    assert model_cost_cents("", 1_000_000, 1_000_000, 0) == 0.0


def test_unknown_paid_model_uses_conservative_default():
    """Fail closed on cost, same as the enforcer: an unlisted paid model
    shows a conservative estimate, never an invisible ¢0.0."""
    cost = model_cost_cents("somevendor/brand-new-model", 1_000_000, 0, 0)
    assert cost > 0


def test_cached_tokens_are_discounted():
    full = model_cost_cents("openai/gpt-oss-120b", 1_000_000, 0, 0)
    mostly_cached = model_cost_cents("openai/gpt-oss-120b", 1_000_000, 0, 900_000)
    assert mostly_cached < full


def test_agrees_with_enforcer_for_every_priced_model():
    """Whatever llm prices, stats must price identically — the UI number
    and the budget-gate number are the same quantity."""
    from homunculus.llm import _MODEL_PRICING_CENTS

    for model, (cin, cout) in _MODEL_PRICING_CENTS.items():
        got = model_cost_cents(model, 2_000_000, 500_000, 0)
        expected = (2_000_000 * cin + 500_000 * cout) / 1_000_000
        assert abs(got - expected) < 1e-6, model
    # And the delegation target itself resolves for the primary.
    assert _pricing_for("openai/gpt-oss-120b") is not None
