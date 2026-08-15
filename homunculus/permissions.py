"""Declarative gate on tool execution.

Every tool call passes through a policy before it runs. The policy can do
three things, and the third is the one that matters:

  * **allow** — the call runs as the model asked.
  * **deny** — the call never runs; the reason becomes the tool result, so
    the refusal is a steering signal the model reads rather than a silent drop.
  * **allow with corrected arguments** — the call runs, but on arguments the
    harness fixed first.

That third case is the point of the module. Everywhere else in the harness a
malformed tool call costs a round trip: the guard rejects it, the model reads
the rejection, and the model tries again — three LLM calls to fix something
deterministic code already understood. A normalizer repairs the arguments in
place and the call proceeds, so a known-shape defect costs nothing.

Relationship to the other gates:

  * `Agent._pre_execute_hook` is *run-scoped and dynamic* — the TaskGuard
    deciding whether this particular run has met its delivery criteria.
  * This policy is *static and declarative* — what is permissible at all,
    independent of any one run.

Both are consulted; the policy runs first, because a call that isn't permitted
never needs run-specific reasoning. Neither replaces `output_guard`, which
judges the assistant's *reply* after the fact rather than the call before it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

# Tools that change durable state. Named here rather than in `output_guard`
# because permission decisions are the lower-level concern — the guard imports
# this set for its own mutation-promise check so the two can never disagree.
MUTATING_TOOLS = frozenset({
    "propose_skill", "create_task", "update_task", "delete_task", "cancel_task",
    "complete_task", "record_failure", "write_file", "append_file", "remember",
    "schedule_next_tick", "update_world_state", "quiz_pick",
})

# `default`     — rules apply exactly as declared.
# `readonly`    — every mutating tool is denied regardless of rules. Suits a
#                 reflection tick, which is meant to read and propose, not act.
# `autonomous`  — nobody is at the keyboard, so `confirm` collapses to `deny`.
#                 The heartbeat runs here.
# `bypass`      — rules are skipped entirely. Normalizers still run: correcting
#                 a malformed argument is a matter of correctness, not
#                 permission, and stays on in every mode.
PermissionMode = Literal["default", "readonly", "autonomous", "bypass"]

Behavior = Literal["allow", "deny", "confirm"]


@dataclass(frozen=True)
class Decision:
    """The outcome of checking one tool call.

    `args` is always the argument dict to execute with — identical to the
    input unless a normalizer corrected it. `corrected` says whether that
    happened, so the caller can record the repair without diffing dicts.
    """

    allowed: bool
    args: dict[str, Any]
    message: str = ""
    corrected: bool = False


@dataclass(frozen=True)
class PermissionRule:
    """One entry in the policy. `tool` is an exact tool name or `"*"`.

    Rules are evaluated in order and the first match wins, so a specific
    rule placed before a `"*"` rule overrides it.
    """

    tool: str
    behavior: Behavior
    reason: str = ""

    def matches(self, name: str) -> bool:
        return self.tool == "*" or self.tool == name


# A normalizer inspects one call and returns corrected arguments, or None when
# it has nothing to change. Returning None (rather than the unchanged dict)
# is what lets the caller distinguish "repaired" from "already fine".
Normalizer = Callable[[str, dict], "dict[str, Any] | None"]


# The model this harness runs on intermittently emits its response-channel
# markup inside the values it sends, producing arguments like
# "bangalore<|channel|>commentary". The intended value is unambiguous, so the
# harness recovers it instead of spending a turn on a lookup that would fail.
_MARKUP_SENTINEL = "<|"


def strip_channel_markup(name: str, args: dict) -> dict[str, Any] | None:
    """Cut response-channel markup out of string arguments."""
    fixed: dict[str, Any] = {}
    changed = False
    for key, value in args.items():
        if isinstance(value, str) and _MARKUP_SENTINEL in value:
            trimmed = value.split(_MARKUP_SENTINEL, 1)[0].strip()
            fixed[key] = trimmed
            changed = True
        else:
            fixed[key] = value
    return fixed if changed else None


def clean_tool_name(name: str) -> str:
    """Recover the intended tool name when channel markup leaks into it.

    The name arrives outside the argument dict, so it is normalized on its
    own rather than through a `Normalizer`.
    """
    if _MARKUP_SENTINEL not in name:
        return name
    return name.split(_MARKUP_SENTINEL, 1)[0].strip() or name


# Identity arguments a scheduled run must never take from the model. The tool
# layer deliberately allows looking up other people ("how is torvalds doing?"),
# which is legitimate in chat and never legitimate in an unattended task: there
# is no user there to have asked. A weekly run once called
# github_profile(user="system") -- a real account -- and reported that
# stranger's 72 followers as the operator's. Nothing downstream could catch it,
# because the numbers were genuinely fetched; only the identity was invented.
_IDENTITY_ARGS: dict[str, str] = {"github_profile": "user"}


def pin_operator_identity(operator: str) -> Normalizer:
    """Correct an identity argument back to the operator's configured handle.

    A normalizer, not a rule: substituting a known-correct identity for a
    guessed one is repairing the call, not refusing it, so it applies in every
    permission mode. Returns None (no change) when the argument is absent or
    already correct, so a run that got it right is untouched.
    """
    wanted = (operator or "").strip()

    def _pin(name: str, args: dict) -> dict[str, Any] | None:
        field = _IDENTITY_ARGS.get(name)
        if not field or not wanted:
            return None
        supplied = str(args.get(field) or "").strip()
        if not supplied or supplied.lower() == wanted.lower():
            return None
        return {**args, field: wanted}

    return _pin


DEFAULT_NORMALIZERS: tuple[Normalizer, ...] = (strip_channel_markup,)


_CONFIRM_MESSAGE = (
    "BLOCKED: '{tool}' needs the user's approval before it can run. "
    "Explain what you intend to do and ask them to confirm; call it again "
    "once they have agreed."
)

_DENY_MESSAGE = "BLOCKED: '{tool}' is not permitted{because}."

_READONLY_MESSAGE = (
    "BLOCKED: '{tool}' changes durable state and this run is read-only. "
    "Gather what you need and report your findings instead."
)


@dataclass(frozen=True)
class PermissionPolicy:
    """What a run is allowed to do, and how malformed calls get repaired."""

    mode: PermissionMode = "default"
    rules: tuple[PermissionRule, ...] = ()
    normalizers: tuple[Normalizer, ...] = field(default=DEFAULT_NORMALIZERS)

    def with_mode(self, mode: PermissionMode) -> PermissionPolicy:
        """Return a copy in a different mode, sharing rules and normalizers."""
        return replace(self, mode=mode)

    def _behavior_for(self, name: str) -> tuple[Behavior, str]:
        for rule in self.rules:
            if rule.matches(name):
                return rule.behavior, rule.reason
        return "allow", ""

    def check(self, name: str, args: dict) -> Decision:
        """Decide whether `name` may run, and on which arguments.

        Normalizers run first and in every mode — a call that is about to be
        denied is still normalized, so the recorded arguments describe what the
        model meant rather than what it mistyped.
        """
        working = dict(args)
        corrected = False
        for normalize in self.normalizers:
            try:
                fixed = normalize(name, working)
            except Exception:
                # A normalizer is an optimization. If one raises, the original
                # arguments are still a valid thing to execute.
                continue
            if fixed is not None:
                working = fixed
                corrected = True

        if self.mode == "bypass":
            return Decision(True, working, corrected=corrected)

        if self.mode == "readonly" and name in MUTATING_TOOLS:
            return Decision(
                False, working, _READONLY_MESSAGE.format(tool=name), corrected
            )

        behavior, reason = self._behavior_for(name)

        if behavior == "confirm" and self.mode == "autonomous":
            behavior = "deny"
            reason = reason or "it needs a person to confirm and this run is unattended"

        if behavior == "allow":
            return Decision(True, working, corrected=corrected)
        if behavior == "confirm":
            return Decision(
                False, working, _CONFIRM_MESSAGE.format(tool=name), corrected
            )
        because = f" — {reason}" if reason else ""
        return Decision(
            False, working, _DENY_MESSAGE.format(tool=name, because=because), corrected
        )


def policy_from_mode(mode: str | None) -> PermissionPolicy:
    """Build a policy from a mode name, falling back to `default`.

    An unrecognized mode is treated as `default` rather than raising: a typo in
    a deployment's environment should not stop the agent from starting, and
    `default` is the permissive-but-ruled setting a fresh install expects.
    """
    valid = ("default", "readonly", "autonomous", "bypass")
    resolved: PermissionMode = mode if mode in valid else "default"  # type: ignore[assignment]
    return PermissionPolicy(mode=resolved)
