"""HomunculusConfig dataclass.

Pins that defaults match the legacy module-level constants (so
migration PRs are pure organisation, not behaviour change), that env
overrides parse correctly, and that the config is frozen + typo-safe.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import (
    CacheConfig,
    HomunculusConfig,
    LoopConfig,
    ProviderConfig,
    TaskLifecycleConfig,
    get_config,
    set_config,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_config(None)
    yield
    set_config(None)


# Defaults must match the legacy constants the old modules exported.

def test_loop_defaults():
    loop = LoopConfig()
    assert loop.max_turns == 20
    assert loop.compact_trigger_user_turns == 8
    assert loop.compact_keep_recent_user_turns == 4
    assert loop.tool_result_hard_cap_chars == 6000
    assert loop.read_file_max_chars == 16_000


def test_task_defaults():
    task = TaskLifecycleConfig()
    assert task.run_history_cap == 20
    assert task.consecutive_failure_limit == 3
    assert task.partial_retry_minutes == 10
    assert task.max_consecutive_partials == 3
    assert task.re_fire_suppression_seconds == 30 * 60
    assert task.advance_due_max_iters == 366 * 2


def test_provider_defaults():
    provider = ProviderConfig()
    assert provider.cooldown_seconds == 60.0
    assert provider.unavailable_cooldown_seconds == 600.0
    assert provider.primary_max_retry_wait == 45.0
    assert provider.primary_default_retry_wait == 8.0
    assert provider.enforce_daily_budget is True


def test_cache_defaults():
    cache = CacheConfig()
    assert cache.web_search_seconds == 3600
    assert cache.web_fetch_seconds == 6 * 3600


def test_top_level_composition():
    config = HomunculusConfig()
    assert isinstance(config.loop, LoopConfig)
    assert isinstance(config.task, TaskLifecycleConfig)
    assert isinstance(config.provider, ProviderConfig)
    assert isinstance(config.cache, CacheConfig)


# Range validation — invalid values must fail at construction.

@pytest.mark.parametrize("kwargs", [
    {"max_turns": 0},
    {"max_turns": -1},
    {"compact_trigger_user_turns": 0},
    {"tool_result_hard_cap_chars": 0},
])
def test_loop_rejects_non_positive(kwargs):
    with pytest.raises(ValidationError):
        LoopConfig(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"cooldown_seconds": 0.0},
    {"cooldown_seconds": -1.0},
    {"primary_max_retry_wait": -0.1},
])
def test_provider_rejects_non_positive(kwargs):
    with pytest.raises(ValidationError):
        ProviderConfig(**kwargs)


def test_unknown_field_rejected():
    """extra='forbid' catches typos. LoopConfig(max_steps=...) is a
    misspelling of max_turns; without the check it'd silently take
    the default."""
    with pytest.raises(ValidationError):
        LoopConfig(max_steps=99)


def test_frozen_config_rejects_mutation():
    config = HomunculusConfig()
    with pytest.raises(ValidationError):
        config.loop.max_turns = 99  # type: ignore[misc]


# Env-driven construction via from_env.

def test_env_override_loop_int(monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_MAX_TURNS", "5")
    config = HomunculusConfig.from_env()
    assert config.loop.max_turns == 5


def test_env_override_provider_float(monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_PROVIDER_COOLDOWN_SECONDS", "12.5")
    config = HomunculusConfig.from_env()
    assert config.provider.cooldown_seconds == 12.5


@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("True", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_env_override_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("HOMUNCULUS_PROVIDER_ENFORCE_DAILY_BUDGET", raw)
    config = HomunculusConfig.from_env()
    assert config.provider.enforce_daily_budget == expected


def test_env_bad_value_raises(monkeypatch):
    """Malformed env values must fail loudly at startup, not
    silently fall back to defaults."""
    monkeypatch.setenv("HOMUNCULUS_MAX_TURNS", "twenty")
    with pytest.raises(ValueError, match="HOMUNCULUS_MAX_TURNS"):
        HomunculusConfig.from_env()


def test_env_cache_uses_web_prefix(monkeypatch):
    """Cache TTLs keep their existing WEB_ prefix for backwards-compat
    with the deployed .env file."""
    monkeypatch.setenv("WEB_SEARCH_CACHE_SECONDS", "600")
    config = HomunculusConfig.from_env()
    assert config.cache.web_search_seconds == 600


def test_missing_env_uses_default(monkeypatch):
    for name in ("HOMUNCULUS_MAX_TURNS", "HOMUNCULUS_COMPACT_TRIGGER_USER_TURNS"):
        monkeypatch.delenv(name, raising=False)
    config = HomunculusConfig.from_env()
    assert config.loop.max_turns == 20
    assert config.loop.compact_trigger_user_turns == 8


def test_empty_env_value_treated_as_unset(monkeypatch):
    """An empty string env var should not be parsed as 0 — it means
    'unset', defer to the default."""
    monkeypatch.setenv("HOMUNCULUS_MAX_TURNS", "")
    config = HomunculusConfig.from_env()
    assert config.loop.max_turns == 20


# Singleton helper.

def test_get_config_caches(monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_MAX_TURNS", "11")
    a = get_config()
    b = get_config()
    assert a is b
    assert a.loop.max_turns == 11


def test_set_config_overrides_singleton():
    custom = HomunculusConfig(loop=LoopConfig(max_turns=3))
    set_config(custom)
    assert get_config().loop.max_turns == 3


def test_set_config_none_reverts_to_env(monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_MAX_TURNS", "7")
    set_config(HomunculusConfig(loop=LoopConfig(max_turns=99)))
    assert get_config().loop.max_turns == 99
    set_config(None)
    assert get_config().loop.max_turns == 7
