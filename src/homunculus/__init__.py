"""Homunculus: an always-on personal agent."""

import os
import sys
from pathlib import Path

import uvicorn
from platformdirs import user_data_path

from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals
from homunculus.model import OpenRouterModel
from homunculus.server import create_app
from homunculus.tools.files import file_tools

PORT = 8765
# The web console's production build, present after `npm run build` in web/.
CONSOLE = Path(__file__).resolve().parents[2] / "web" / "dist"


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Set OPENROUTER_API_KEY to an OpenRouter API key, then start again.")
    # The data directory lives outside the code repository, so private data cannot be
    # committed. By default it is the operating system's place for application data.
    home = Path(os.environ.get("HOMUNCULUS_HOME") or user_data_path("homunculus", appauthor=False))
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    app = create_app(
        OpenRouterModel(api_key),
        file_tools(workspace),
        ActivityLog(home / "activity.jsonl"),
        Approvals(home / "approvals.json"),
        console=CONSOLE if (CONSOLE / "index.html").is_file() else None,
    )
    # Bound to this machine only: nothing else on the network can reach the API.
    uvicorn.run(app, host="127.0.0.1", port=PORT)
