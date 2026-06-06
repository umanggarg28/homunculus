"""Sandbox tools: python (sibling container), shell_exec (REPL-only)."""

from __future__ import annotations

import subprocess

from config import get_config

from ._state import is_autonomous


def python_exec(code: str, timeout: int = 30) -> str:
    """Run Python code in an ephemeral sandbox container.

    Each call spawns a fresh python:3.12-slim sibling container via the
    host's Docker daemon. Container is destroyed when it exits.
    Network=none, memory=256m, cpus=0.5, read-only fs, 30s wall-clock.
    """
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network=none",
        "--memory=256m",
        "--cpus=0.5",
        "--pids-limit=50",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "python:3.12-slim",
        "python", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: code execution timed out after {timeout}s"
    except FileNotFoundError:
        return (
            "ERROR: docker CLI not found. The python tool needs "
            "/var/run/docker.sock mounted and the docker CLI in the image. "
            "Check docker-compose.yml and Dockerfile."
        )

    parts: list[str] = []
    if result.stdout:
        parts.append(f"STDOUT:\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"STDERR:\n{result.stderr.rstrip()}")
    if not parts:
        parts.append(f"(no output, exit code {result.returncode})")
    elif result.returncode != 0:
        parts.append(f"(exit code: {result.returncode})")

    output = "\n\n".join(parts)
    max_chars = get_config().loop.read_file_max_chars
    if len(output) > max_chars:
        output = (
            output[:max_chars]
            + f"\n[...{len(output) - max_chars} chars truncated]"
        )
    return output


def shell_exec(command: str) -> str:
    """Run a shell command. REPL: user must approve. Autonomous: refused."""
    if is_autonomous():
        return (
            "BLOCKED: shell_exec is disabled in autonomous (heartbeat) mode. "
            "If you really need this command run, call remember() to leave "
            "a note for the user describing what you want and why; they'll "
            "execute it next REPL session."
        )
    print(f"\n  [agent wants to run]: {command}")
    answer = input("  approve? [y/N]: ").strip().lower()
    if answer != "y":
        return "DENIED by user"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"
    output = (result.stdout + result.stderr).strip()
    if not output:
        return f"(exit {result.returncode}, no output)"
    if len(output) > 4000:
        return output[:4000] + "\n[...truncated]"
    return output


