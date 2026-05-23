"""Memory tools: remember, forget."""

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


