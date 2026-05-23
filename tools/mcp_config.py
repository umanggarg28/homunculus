"""Load and validate `homunculus.yaml` — the MCP server registry.

A single config file at the repo root lists every MCP server the agent
should connect to. Each entry declares how to launch the server
(`command`) and what it's allowed to do (`permissions`). The manager
reads this file, the manager owns the lifecycle.

Schema (loose, validated at load time):

    servers:
      <name>:
        command: [str, ...]          # argv, passed to subprocess
        env: {str: str}              # optional
        cwd: str                     # optional
        permissions:
          mutating: bool             # default false; gates plan-mode
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "homunculus.yaml"


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    mutating: bool = False


@dataclass(frozen=True)
class Config:
    servers: list[ServerConfig]

    def by_name(self, name: str) -> ServerConfig | None:
        for s in self.servers:
            if s.name == name:
                return s
        return None


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"MCP config not found at {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    servers_raw = (raw.get("servers") or {})
    if not isinstance(servers_raw, dict):
        raise ValueError("`servers` must be a mapping of name → config")

    servers: list[ServerConfig] = []
    for name, entry in servers_raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"servers.{name} must be a mapping")
        command = entry.get("command")
        if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
            raise ValueError(f"servers.{name}.command must be a list of strings")
        perms = entry.get("permissions") or {}
        servers.append(
            ServerConfig(
                name=name,
                command=list(command),
                env=dict(entry.get("env") or {}),
                cwd=entry.get("cwd"),
                mutating=bool(perms.get("mutating", False)),
            )
        )

    return Config(servers=servers)
