"""Persistent autonomy controls for Homunculus.

The agent is intentionally small, so controls live in one JSON file instead of
adding a database dependency. The backend and tests both use this module; the
web UI edits it through /api/agent/controls.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MAX_STEPS = int(os.environ.get("HOMUNCULUS_MAX_TURNS", "20"))
DEFAULT_CONTROLS_PATH = Path(
    os.environ.get("HOMUNCULUS_AGENT_CONTROLS_PATH", "./memory/agent_controls.json")
)


@dataclass
class AgentControls:
    max_steps: int = DEFAULT_MAX_STEPS
    dry_run: bool = False
    prefer_free_models: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)

    def normalized(self) -> "AgentControls":
        max_steps = min(50, max(1, int(self.max_steps or DEFAULT_MAX_STEPS)))
        allowed = sorted({str(x).strip() for x in self.allowed_tools if str(x).strip()})
        blocked = sorted({str(x).strip() for x in self.blocked_tools if str(x).strip()})
        return AgentControls(
            max_steps=max_steps,
            dry_run=bool(self.dry_run),
            prefer_free_models=bool(self.prefer_free_models),
            allowed_tools=allowed,
            blocked_tools=blocked,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())


def controls_path() -> Path:
    return Path(os.environ.get("HOMUNCULUS_AGENT_CONTROLS_PATH", str(DEFAULT_CONTROLS_PATH)))


def load_controls(path: Path | None = None) -> AgentControls:
    path = path or controls_path()
    if not path.exists():
        return AgentControls().normalized()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AgentControls().normalized()
    if not isinstance(data, dict):
        return AgentControls().normalized()
    return AgentControls(
        max_steps=data.get("max_steps", DEFAULT_MAX_STEPS),
        dry_run=data.get("dry_run", False),
        prefer_free_models=data.get("prefer_free_models", True),
        allowed_tools=data.get("allowed_tools") or [],
        blocked_tools=data.get("blocked_tools") or [],
    ).normalized()


def save_controls(updates: dict[str, Any], path: Path | None = None) -> AgentControls:
    current = load_controls(path)
    data = current.to_dict()
    for key in ("max_steps", "dry_run", "prefer_free_models", "allowed_tools", "blocked_tools"):
        if key in updates:
            data[key] = updates[key]
    next_controls = AgentControls(
        max_steps=data.get("max_steps", DEFAULT_MAX_STEPS),
        dry_run=data.get("dry_run", False),
        prefer_free_models=data.get("prefer_free_models", True),
        allowed_tools=data.get("allowed_tools") or [],
        blocked_tools=data.get("blocked_tools") or [],
    ).normalized()
    path = path or controls_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(next_controls.to_dict(), indent=2) + "\n", encoding="utf-8")
    return next_controls


def tool_block_reason(tool_name: str, *, is_mutating: bool, controls: AgentControls | None = None) -> str | None:
    controls = controls or load_controls()
    if controls.allowed_tools and tool_name not in set(controls.allowed_tools):
        return f"tool '{tool_name}' is not in allowed_tools"
    if tool_name in set(controls.blocked_tools):
        return f"tool '{tool_name}' is blocked by agent controls"
    if controls.dry_run and is_mutating:
        return f"dry_run is enabled; mutating tool '{tool_name}' was not executed"
    return None


def prefer_free_models() -> bool:
    return load_controls().prefer_free_models
