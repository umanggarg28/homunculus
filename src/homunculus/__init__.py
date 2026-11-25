"""Homunculus: an always-on personal agent."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from platformdirs import user_data_path

from homunculus import identifiers, importing
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.limits import Budget
from homunculus.limits import load as load_limits
from homunculus.memory import Memory
from homunculus.model import OpenRouterModel
from homunculus.sandbox import docker
from homunculus.sandbox.docker import DockerSandbox
from homunculus.server import create_app
from homunculus.skills import Skills
from homunculus.tools.files import file_tools
from homunculus.tools.shell import shell_tool
from homunculus.waker import RoutinesError, Waker, load_routines
from homunculus.waker import load_settings as load_waker_settings

PORT = 8765
# The web console's production build, present after `npm run build` in web/.
CONSOLE = Path(__file__).resolve().parents[2] / "web" / "dist"
# Generic skills, published with the code. Personal ones live in the data directory.
BUILTIN_SKILLS = Path(__file__).resolve().parents[2] / "skills"


def data_home() -> Path:
    """The data directory. It lives outside the code repository, so private data cannot
    be committed; by default it is the operating system's place for application data."""
    home = Path(os.environ.get("HOMUNCULUS_HOME") or user_data_path("homunculus", appauthor=False))
    home.mkdir(parents=True, exist_ok=True)
    return home


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="homunculus", description="An always-on personal agent.")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("serve", help="Start the agent and its web console (the default).")
    bring = commands.add_parser(
        "import", help="Copy the files listed in import.txt into memory, after a scan."
    )
    bring.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PATH",
        help="Include a file the scan left out, once you have looked at it. Can be repeated.",
    )
    check = commands.add_parser(
        "check-identifiers", help="Stop a commit whose staged lines mention your identifiers."
    )
    check.add_argument("--staged", action="store_true", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "import":
        sys.exit(importing.run(data_home(), arguments.allow))
    if arguments.command == "check-identifiers":
        sys.exit(identifiers.check_staged(data_home()))
    serve()


def serve() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Set OPENROUTER_API_KEY to an OpenRouter API key, then start again.")
    home = data_home()
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    activity = Activity(home)
    # Memory sits beside the workspace, never inside it, so the sandbox cannot reach it.
    memory = Memory.open(home / "memory")
    (home / "skills").mkdir(exist_ok=True)
    skills = Skills(BUILTIN_SKILLS, home / "skills", identifiers=lambda: identifiers.load(home))
    found = skills.found()
    for problem in skills.problems():
        print(problem, file=sys.stderr)
    tools = file_tools(workspace, memory, skills)
    # The sandbox mounts the skills found now; one added later can be read straight
    # away, and its scripts run after a restart.
    sandbox = DockerSandbox(workspace, skills={name: s.folder for name, s in found.items()})
    # Commands run only inside the sandbox. Without it there is no shell tool at all,
    # never a shell on this machine.
    try:
        if not docker.available():
            raise RuntimeError("Docker is not running")
        sandbox.start()
    except RuntimeError as reason:
        print(f"{reason}, so the agent has no shell tool this time.", file=sys.stderr)
    else:
        sandbox.record_requests(activity)
        tools.append(shell_tool(sandbox))
    model = OpenRouterModel(api_key)
    limits = load_limits(home)
    # The waker decides when the agent works on its own; its two tools let the agent set
    # and cancel its own wake-ups.
    settings = load_waker_settings(home)
    try:
        routines = load_routines(home, settings.zone())
    except RoutinesError as error:
        sandbox.stop()
        sys.exit(f"routines.toml: {error}")
    waker = Waker(home / "waker.json", settings, limits, routines=routines)
    tools.extend(waker.tools())
    app = create_app(
        model,
        tools,
        activity,
        Approvals(home / "approvals.json"),
        console=CONSOLE if (CONSOLE / "index.html").is_file() else None,
        memory=memory,
        limits=limits,
        budget=Budget(limits, model.month_spend),
        waker=waker,
        skills=skills,
    )
    try:
        # Bound to this machine only: nothing else on the network can reach the API.
        uvicorn.run(app, host="127.0.0.1", port=PORT)
    finally:
        sandbox.stop()
