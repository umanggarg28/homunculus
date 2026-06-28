"""System prompt order pins for provider cache hit rate.

Gemini's implicit cache + OpenRouter's explicit cache_control both
prefix-match. Volatile pieces (date, world_state, rate-limit signal)
must come AFTER stable pieces (base prompt, AGENTS.md, loadable
tools) or the cache invalidates on every turn. Pre-reorder we
measured ~40% hit rate on Gemini; the order below targets 70%+.

If a future change re-introduces a volatile component into the
cacheable prefix, these tests fail loudly so the regression is
caught before it shows up as a budget spike.
"""

from __future__ import annotations


def test_date_line_comes_after_loadable_tools(tmp_path, monkeypatch):
    from homunculus import core
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(tmp_path / "nope.md"))
    agent = core.Agent()
    prompt = agent._current_system_prompt()
    date_marker = "Current date/time:"
    loadable_marker = "# Loadable tools"
    if loadable_marker in prompt:
        assert prompt.index(loadable_marker) < prompt.index(date_marker), (
            "regression: date line is before the loadable-tools catalogue. "
            "This invalidates the implicit provider cache on every turn."
        )


def test_agents_md_comes_before_date_line(tmp_path, monkeypatch):
    """AGENTS.md is mostly stable — must be cacheable, i.e. before
    the volatile date line."""
    from homunculus import core
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text("# Persona\nbe terse", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(agents_path))
    agent = core.Agent()
    prompt = agent._current_system_prompt()
    assert prompt.index("AGENTS.md") < prompt.index("Current date/time:"), (
        "AGENTS.md must come before the volatile date line so it stays "
        "in the cacheable prefix."
    )


def test_world_state_comes_after_stable_prefix(tmp_path, monkeypatch):
    """World state changes every turn — must be at the end of the
    prompt, after the cacheable prefix."""
    from homunculus import core
    from homunculus.memory import Memory
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(tmp_path / "nope.md"))
    mem = Memory(tmp_path)
    mem.world_state.update({"focus": "testing", "step": 1})
    agent = core.Agent(memory=mem)
    prompt = agent._current_system_prompt()
    world_marker = "Session world state"
    # World state must appear; and must come AFTER the date line
    # (which itself comes after the cacheable prefix).
    assert world_marker in prompt
    assert prompt.index("Current date/time:") < prompt.index(world_marker)


def test_rate_limit_heads_up_is_last_when_present(monkeypatch):
    """Rate-limit signal is sporadic; when present it must be the
    final piece so its absence/presence doesn't shift the cache key
    of everything before it."""
    from homunculus import core, llm
    monkeypatch.setattr(llm, "_PROVIDER_LAST_COOLED_AT", core.time.time())
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", "/nope")
    agent = core.Agent()
    prompt = agent._current_system_prompt()
    if "# Heads up" in prompt:
        # Heads up appears → must be after every other section.
        heads_idx = prompt.index("# Heads up")
        for earlier in ("Current date/time:", "Session world state"):
            if earlier in prompt:
                assert prompt.index(earlier) < heads_idx
