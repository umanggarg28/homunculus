"""Memory tools: remember, forget, search_memory."""

from __future__ import annotations

from ._state import get_memory


def remember(
    name: str,
    description: str,
    type: str,
    body: str,
    related: list[str] | None = None,
) -> str:
    mem = get_memory()
    if mem is None:
        return "ERROR: memory subsystem is not initialized"
    return mem.remember(
        name=name, description=description, type=type, body=body, related=related,
    )


def forget(name: str) -> str:
    mem = get_memory()
    if mem is None:
        return "ERROR: memory subsystem is not initialized"
    return mem.forget(name)


def search_memory(query: str) -> str:
    """Search long-term memory for snippets matching the query.

    Backing function for the recall() MCP tool. The agent calls this
    explicitly — nothing is auto-injected into context. Returns top
    matching memory bodies with age tags, or a 'not found' message.
    """
    mem = get_memory()
    if mem is None:
        return "ERROR: memory subsystem is not initialized"
    result = mem.search(query, limit=3, max_chars=900)
    if not result:
        return f"No relevant memories found for query: {query!r}"
    return result


def get_world_state() -> dict:
    mem = get_memory()
    if mem is None:
        return {}
    return mem.get_world_state()


def update_world_state(updates: dict) -> dict:
    mem = get_memory()
    if mem is None:
        return {}
    return mem.update_world_state(updates)


def rate_skill(name: str, outcome: str, notes: str = "") -> str:
    mem = get_memory()
    if mem is None:
        return "ERROR: memory subsystem is not initialized"
    return mem.rate_skill(name, outcome, notes)


