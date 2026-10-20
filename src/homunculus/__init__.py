"""Homunculus: an always-on personal agent."""

import os
import sys
from pathlib import Path

import uvicorn

from homunculus.activity import ActivityLog
from homunculus.model import OpenRouterModel
from homunculus.server import create_app
from homunculus.tools import file_tools

PORT = 8765


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Set OPENROUTER_API_KEY to an OpenRouter API key, then start again.")
    # The data directory lives outside the code repository, so private data cannot be committed.
    home = Path(os.environ.get("HOMUNCULUS_HOME", Path.home() / "homunculus-data"))
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    app = create_app(
        OpenRouterModel(api_key), file_tools(workspace), ActivityLog(home / "activity.jsonl")
    )
    # Bound to this machine only: nothing else on the network can reach the API.
    uvicorn.run(app, host="127.0.0.1", port=PORT)
