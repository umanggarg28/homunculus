"""Single source of truth for runtime tuning.

WHY THIS FILE EXISTS
====================

Tuning knobs were scattered as module-level constants across eight
files (MAX_TURNS in core.py, RUN_HISTORY_CAP in tasks.py,
READ_FILE_MAX_CHARS in tools/_helpers.py, etc.). No single place to
see the system's tuning surface, no validation of ranges, no easy
way to override knobs in tests without monkey-patching modules.

THE PATTERN (Pi / Letta / OpenClaw)
===================================

Pi's `LoopConfig` is one typed object. Letta has `AgentConfig`.
OpenClaw's house rule: "Runtime reads canonical config only. No
silent compat for old / malformed config keys."

We use plain Pydantic `BaseModel` for the typed shape, and a small
`_load_from_env` helper that walks `model_fields` so the env-loading
code stays DRY without pulling in a new dependency
(pydantic-settings is the idiomatic choice but it forces a dotenv
import that conflicts with our test stubs).

Defaults match the legacy module-level constants exactly so the
migration PRs that follow are pure organisation, not behaviour
change. Per-field range validation (Annotated `Field(ge=1)`) catches
typos like `LoopConfig(max_steps=99)` at construction time instead
of letting them silently take the default.

This file adds zero runtime cost. Callsite migration — replacing
`from core import MAX_TURNS` with `get_config().loop.max_turns` —
follows in dedicated PRs.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Self-documenting numeric bounds
# ---------------------------------------------------------------------------

PositiveInt = Annotated[int, Field(ge=1)]
PositiveFloat = Annotated[float, Field(gt=0)]


# ---------------------------------------------------------------------------
# Sub-configs grouped by concern
# ---------------------------------------------------------------------------
#
# Every sub-config is frozen + extra=forbid:
#   * frozen prevents accidental mutation at runtime
#   * extra=forbid surfaces typos at construction
#     (LoopConfig(max_steps=99) would silently take the default if
#      extras were allowed)


_STRICT = ConfigDict(frozen=True, extra="forbid")


class LoopConfig(BaseModel):
    """Per-turn loop ceilings and compaction triggers."""

    model_config = _STRICT

    max_turns: PositiveInt = Field(
        # 28: a long application form legitimately needs ~20 sequential
        # tool calls (prepare + context + one draft_answer per question)
        # — the budget enforcer, not this cap, is the runaway backstop.
        default=28,
        description=(
            "Iteration cap per chat/heartbeat turn. The loop yields a "
            "fallback string after this many LLM calls without a final reply."
        ),
    )
    compact_trigger_user_turns: PositiveInt = Field(
        default=8,
        description=(
            "User-turn count above which we summarise older turns. Counts "
            "USER messages, not raw history length — tool-heavy assistant "
            "turns don't trigger compaction."
        ),
    )
    compact_keep_recent_user_turns: PositiveInt = Field(
        default=4,
        description="User-turn count kept verbatim after compaction.",
    )
    tool_result_hard_cap_chars: PositiveInt = Field(
        default=6000,
        description=(
            "Hard cap on chars per tool result entering history. "
            "Per-tool overrides in core._PER_TOOL_RESULT_CAPS win."
        ),
    )
    read_file_max_chars: PositiveInt = Field(
        default=16_000,
        description=(
            "Cap on chars returned by read_file. Above this the tool "
            "returns the tail with a head-truncation marker."
        ),
    )


class TaskLifecycleConfig(BaseModel):
    """Task store retention, retry, and circuit-breaker policy."""

    model_config = _STRICT

    run_history_cap: PositiveInt = Field(default=20)
    consecutive_failure_limit: PositiveInt = Field(default=3)
    partial_retry_minutes: PositiveInt = Field(default=10)
    max_consecutive_partials: PositiveInt = Field(default=3)
    re_fire_suppression_seconds: PositiveInt = Field(default=30 * 60)
    advance_due_max_iters: PositiveInt = Field(default=366 * 2)


class ProviderConfig(BaseModel):
    """Provider-chain cooldowns + budget enforcement."""

    model_config = _STRICT

    cooldown_seconds: PositiveFloat = Field(default=60.0)
    unavailable_cooldown_seconds: PositiveFloat = Field(default=10 * 60.0)
    primary_max_retry_wait: PositiveFloat = Field(default=45.0)
    primary_default_retry_wait: PositiveFloat = Field(default=8.0)
    enforce_daily_budget: bool = Field(default=True)
    drafting_model: str = Field(
        default="anthropic/claude-sonnet-5",
        description=(
            "Model for harness-owned judgment calls (drafting job-application "
            "answers). These are few, bounded, and quality-critical — user-"
            "facing prose sent to third parties — so they route to a strong "
            "model while the routine agent loop stays on the cheap primary. "
            "Still budget-enforced like every paid call."
        ),
    )


class CacheConfig(BaseModel):
    """TTLs for the disk cache that fronts web-touching tools."""

    model_config = _STRICT

    web_search_seconds: PositiveInt = Field(default=3600)
    web_fetch_seconds: PositiveInt = Field(default=6 * 3600)


class PermissionConfig(BaseModel):
    """Default posture for the tool-execution gate (`permissions.py`).

    Only the mode is configurable here. Rules are per-run rather than
    deployment-wide — a reflection tick and a chat turn want different ones —
    so callers construct a `PermissionPolicy` and pass it to the `Agent`.
    """

    model_config = _STRICT

    mode: str = Field(default="default")


# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------
#
# One helper walks a sub-model's fields and reads env vars named
# {prefix}_{FIELD_NAME}. Empty / missing → field default. Malformed →
# raise immediately. No duplicated branches per field.


def _parse_env_value(raw: str, py_type: type) -> Any:
    """Coerce an env-var string to the field's Python type. Raises
    ValueError on unparseable input so callers can surface a clear
    startup error instead of silently falling back to a default."""
    if py_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if py_type is int:
        return int(raw)
    if py_type is float:
        return float(raw)
    if py_type is str:
        return raw
    raise TypeError(f"unsupported env field type: {py_type}")


def _load_from_env(
    sub_model: type[BaseModel],
    prefix: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a kwargs dict for `sub_model` from environment variables.

    Default mapping: field `foo_bar` reads env var `{prefix}_FOO_BAR`.
    `overrides` maps a field name to an explicit env var name when
    legacy deployed names don't fit the prefix pattern (e.g.
    `web_search_seconds` reads `WEB_SEARCH_CACHE_SECONDS` to stay
    compatible with .env files that predate this refactor).

    Missing or empty env vars get omitted so the field default
    applies. Bad values raise ValueError with the env name attached.
    """
    overrides = overrides or {}
    kwargs: dict[str, Any] = {}
    for name, field_info in sub_model.model_fields.items():
        env_name = overrides.get(name, f"{prefix}_{name.upper()}")
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            kwargs[name] = _parse_env_value(raw, _unwrap_annotation(field_info.annotation))
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"environment variable {env_name}={raw!r} is invalid: {e}"
            ) from e
    return kwargs


def _unwrap_annotation(ann: Any) -> type:
    """Pull the runtime type out of an Annotated[...] wrapper so
    `_parse_env_value` can branch on it. PositiveInt is
    Annotated[int, Field(ge=1)] — we want the `int`."""
    # typing.Annotated.__origin__ is the base type; __metadata__ is
    # the extras. For non-Annotated types this attribute is missing
    # and we use the type directly.
    return getattr(ann, "__origin__", ann)


# ---------------------------------------------------------------------------
# Top-level config — composes the four sub-configs, env-driven
# ---------------------------------------------------------------------------


class HomunculusConfig(BaseModel):
    """Top-level config. Pass this to subsystems instead of having them
    import module-level constants.

    Env-var names use a `HOMUNCULUS_` prefix and the field name in
    upper case:

        HOMUNCULUS_MAX_TURNS=5
        HOMUNCULUS_PARTIAL_RETRY_MINUTES=15
        HOMUNCULUS_PROVIDER_COOLDOWN_SECONDS=30
        HOMUNCULUS_ENFORCE_DAILY_BUDGET=true
        WEB_SEARCH_CACHE_SECONDS=600

    The flat naming preserves backwards-compat with existing deployed
    `.env` files (HOMUNCULUS_MAX_TURNS, HOMUNCULUS_ENFORCE_DAILY_BUDGET,
    etc.). Future env knobs should follow the same flat scheme.
    """

    model_config = _STRICT

    loop: LoopConfig = Field(default_factory=LoopConfig)
    task: TaskLifecycleConfig = Field(default_factory=TaskLifecycleConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)

    @classmethod
    def from_env(cls) -> HomunculusConfig:
        """Build a config from environment variables.

        Each sub-config reads vars under a prefix that matches how
        constants were named in the legacy module-level setup:

          * loop      ← HOMUNCULUS_
          * task      ← HOMUNCULUS_
          * provider  ← HOMUNCULUS_PROVIDER_  (loud about the family)
          * cache     ← WEB_  (matches existing WEB_SEARCH_CACHE_SECONDS)

        Bad values raise ValueError at startup; missing values take
        the field default.
        """
        return cls(
            loop=LoopConfig(**_load_from_env(LoopConfig, "HOMUNCULUS")),
            task=TaskLifecycleConfig(**_load_from_env(TaskLifecycleConfig, "HOMUNCULUS")),
            provider=ProviderConfig(**_load_from_env(
                ProviderConfig,
                "HOMUNCULUS_PROVIDER",
                # Model selection reads like HOMUNCULUS_MODEL, not like a
                # provider-chain tuning knob — keep the env family together.
                overrides={"drafting_model": "HOMUNCULUS_DRAFTING_MODEL"},
            )),
            cache=CacheConfig(**_load_from_env(
                CacheConfig,
                # Unused — every cache field uses an explicit legacy name.
                prefix="WEB",
                overrides={
                    "web_search_seconds": "WEB_SEARCH_CACHE_SECONDS",
                    "web_fetch_seconds": "WEB_FETCH_CACHE_SECONDS",
                },
            )),
            permission=PermissionConfig(**_load_from_env(
                PermissionConfig,
                "HOMUNCULUS_PERMISSION",
            )),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


_cached: HomunculusConfig | None = None


def get_config() -> HomunculusConfig:
    """Return the process-wide config, building from env on first call."""
    global _cached
    if _cached is None:
        _cached = HomunculusConfig.from_env()
    return _cached


def set_config(config: HomunculusConfig | None) -> None:
    """Override (tests) or clear (force re-read from env on next access)."""
    global _cached
    _cached = config


__all__ = [
    "CacheConfig",
    "HomunculusConfig",
    "LoopConfig",
    "ProviderConfig",
    "TaskLifecycleConfig",
    "get_config",
    "set_config",
]
