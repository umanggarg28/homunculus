"""Homunculus: an always-on personal agent."""

import os
import sys

import uvicorn

from homunculus.model import OpenRouterModel
from homunculus.server import create_app

PORT = 8765


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Set OPENROUTER_API_KEY to an OpenRouter API key, then start again.")
    # Bound to this machine only: nothing else on the network can reach the API.
    uvicorn.run(create_app(OpenRouterModel(api_key)), host="127.0.0.1", port=PORT)
