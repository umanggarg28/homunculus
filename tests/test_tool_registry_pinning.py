"""Every hardcoded tool name must name a tool that exists.

The harness keeps a dozen hand-written tool-name lists across six modules —
which tools are cacheable, which are plumbing, which carry untrusted content,
which are always loaded. Each is a policy the code cannot derive, so each is
typed out by hand, and nothing checked them against the registry: a renamed
or removed tool left its guard silently disabled, and a typo produced a rule
that matched nothing. Adding `no_action` exposed the same gap from the other
side — a genuinely new tool has to be considered against every one of these
lists, and there was no mechanical way to be reminded.

This cannot judge whether a tool BELONGS in a given list; that is a design
decision. It pins the mechanical half: no list may name a tool that is not
in the registry.

The registry is read by AST rather than import, because `homunculus.tools`
pulls in the container-only MCP stack and the suite's conftest replaces it
with an empty stub.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "homunculus" / "tools"


def _registered_tool_names() -> set[str]:
    """Names of every function exposed with @mcp.tool in the MCP server."""
    tree = ast.parse((TOOLS / "mcp_server.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            attr = getattr(target, "attr", None)
            value = getattr(target, "value", None)
            if attr != "tool" or getattr(value, "id", None) != "mcp":
                continue
            # `@mcp.tool(name="python")` overrides the function name, so the
            # exposed name and the def name can differ — reading the def name
            # alone reports a real tool as missing.
            exposed = node.name
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        exposed = kw.value.value
            names.add(exposed)
    return names


def _string_set_literal(path: Path, variable: str) -> set[str]:
    """The string constants assigned to `variable` at module level.

    Handles a frozenset({...}) / set literal / tuple of strings, and the
    dict-keyed policies (a cap table keys on tool names just as a set does).
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(getattr(x, "id", None) == variable for x in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call):  # frozenset({...}) / set([...])
            value = value.args[0] if value.args else None
        found: set[str] = set()
        if isinstance(value, ast.Dict):
            found = {k.value for k in value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            found = {e.value for e in value.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if found:
            return found
    raise AssertionError(f"{variable} not found as a literal in {path.name}")


#: (module, variable) for every hand-written tool-name policy in the harness.
#: A new one belongs here the day it is written.
TOOL_NAME_POLICIES = [
    ("homunculus/tools/__init__.py", "ALWAYS_LOADED"),
    ("homunculus/core.py", "READ_ONLY_CACHEABLE_TOOLS"),
    ("homunculus/core.py", "_TAIL_PRESERVING_TOOLS"),
    ("homunculus/core.py", "_TERMINAL_TASK_TOOLS"),
    ("homunculus/core.py", "_PER_TOOL_RESULT_CAPS"),
    ("homunculus/core.py", "DEFAULT_TOOL_TURN_CAPS"),
    ("homunculus/doctor.py", "_HARNESS_TOOLS"),
    ("homunculus/security.py", "_UNTRUSTED_CONTENT_TOOLS"),
    ("homunculus/security.py", "SENSITIVE_RESULT_TOOLS"),
    ("homunculus/output_guard.py", "_WEB_GROUNDING_TOOLS"),
    ("homunculus/output_guard.py", "_SCHEDULING_TOOLS"),
    ("homunculus/output_guard.py", "_CLAIM_TARGET_TOOLS"),
    ("homunculus/permissions.py", "_TEMPLATE_EXEMPT_TOOLS"),
    ("homunculus/permissions.py", "MUTATING_TOOLS"),
    ("homunculus/heartbeat.py", "_REFLECTION_CALL_CAPS"),
    ("homunculus/heartbeat.py", "_REFLECTION_FORBIDDEN"),
]


def test_the_registry_is_readable():
    """If this fails the rest are vacuous, so assert it explicitly."""
    names = _registered_tool_names()
    assert len(names) > 40, f"only found {len(names)} @mcp.tool functions"
    assert {"notify", "complete_task", "load_tool", "no_action"} <= names


@pytest.mark.parametrize("module,variable", TOOL_NAME_POLICIES)
def test_policy_names_exist_in_the_registry(module, variable):
    registered = _registered_tool_names()
    declared = _string_set_literal(REPO / module, variable)
    unknown = sorted(declared - registered)
    assert not unknown, (
        f"{module}:{variable} names tool(s) that do not exist: {unknown}. "
        "A guard keyed on a missing tool silently does nothing — either the "
        "tool was renamed and this list was not, or the name is a typo."
    )


def test_always_loaded_is_a_subset_of_the_registry():
    """The always-loaded set is sent to the model every turn; a phantom name
    there is a schema the provider never receives and the model never sees."""
    always = _string_set_literal(REPO / "homunculus/tools/__init__.py", "ALWAYS_LOADED")
    assert always <= _registered_tool_names()


def test_no_action_is_reachable_without_loading_it():
    """The escape hatch for a forced turn cannot itself require a tool call."""
    always = _string_set_literal(REPO / "homunculus/tools/__init__.py", "ALWAYS_LOADED")
    assert "no_action" in always


# ------------------------------------------------------- the persona layer

def _agents_md_inventory() -> set[str]:
    """Tool names named anywhere in AGENTS.md.

    Deliberately whole-file rather than section-scoped: an operator disables a
    tool by commenting the line out, and a commented-out entry is still a
    conscious decision about that tool. What must not happen is a tool nobody
    ever mentioned.
    """
    import re

    text = (REPO / "AGENTS.md").read_text()
    return set(re.findall(r"\b[a-z][a-z0-9_]{2,}\b", text))


def test_agents_md_names_no_tool_that_does_not_exist():
    """AGENTS.md is injected into the system prompt every turn, so a phantom
    name there is a capability the model believes it has."""
    registered = _registered_tool_names()
    inventory_section = (REPO / "AGENTS.md").read_text()
    section = inventory_section.split("- read_file, write_file", 1)[1].split("\n\n", 1)[0]
    import re

    claimed = {
        n for n in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", section)
        if n.endswith((
            "_task", "_file", "_files", "_search", "_url", "_feed", "_pick",
            "_grade", "_profile", "_insert", "_time", "_fetch", "_post",
            "_tick", "_summary", "_scratchpad", "_state", "_skill", "_review",
            "_commitment", "_context", "_answer", "_answers", "_problem",
            "_events", "_unread", "_application", "_weather", "_action",
            "_tool", "_consolidation", "_refinement", "_exec", "_proposals",
        ))
    }
    phantom = sorted(claimed - registered)
    assert not phantom, (
        f"AGENTS.md names tool(s) that do not exist: {phantom}. The model "
        "reads this list as authoritative and will try to call them."
    )


def test_every_tool_is_mentioned_in_agents_md():
    """The other direction, and the one that bit hardest.

    A registered tool absent from AGENTS.md is a tool the model is never told
    about — observed live, it denied having `record_commitment`, reciting
    exactly the set this file listed. Adding a tool means deciding whether the
    agent should know about it; this makes that decision explicit rather than
    forgotten.
    """
    missing = sorted(_registered_tool_names() - _agents_md_inventory())
    assert not missing, (
        f"These tools exist but AGENTS.md never mentions them: {missing}. "
        "Add them to the inventory (or comment them out deliberately) — "
        "AGENTS.md is injected into the system prompt every turn, and the "
        "model treats its list as the set of things it can do."
    )
