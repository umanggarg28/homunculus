"""What the model is told at the start of every turn.

The instructions live in `prompt.md` next to this module, as plain markdown, and are
sent exactly as written. They are the program's own rules, so they ship with the code,
change only through a reviewed pull request, and sit outside the workspace, where no
tool can rewrite them. They hold rules for every task, unlike a skill, which is read
only when the model decides it fits, and no facts about the operator.
"""

from importlib.resources import files

# Read once at start-up, so a missing or empty file stops the program before any turn,
# and every turn sends the same text, which a provider can cache.
_INSTRUCTIONS = files("homunculus").joinpath("prompt.md").read_text(encoding="utf-8").strip()
if not _INSTRUCTIONS:
    raise RuntimeError("prompt.md is empty: the agent must not run without its instructions.")


def system_prompt() -> str:
    """The instructions the model reads before the conversation."""
    return _INSTRUCTIONS
