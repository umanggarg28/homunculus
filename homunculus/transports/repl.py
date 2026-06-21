"""
Homunculus REPL — Phase 1 entry point.

This file is the "frontend" for talking to the agent from a terminal.
It loads config, instantiates the agent, and runs a simple read-eval-
print loop. The agent itself lives in core.py and doesn't know or care
that it's being driven by a terminal — that's the whole point of the
separation.

Run (locally with uv):
    cd homunculus/
    uv sync
    uv run python main.py

Run (in container):
    docker compose run --rm homunculus
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from homunculus import tools, REPO_ROOT
from homunculus.core import Agent
from homunculus.memory import Memory

# Where the agent stores its persistent memory on disk. Inside the Docker
# container this resolves under the bind-mounted workspace, so memory
# survives between container runs and is visible from the host.
MEMORY_DIR = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")

    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set. Put it in .env at the repo root or export it.")

    # Wire up memory: instantiate, give the remember() tool a reference,
    # then hand the same instance to the agent so it can load the index.
    memory = Memory(MEMORY_DIR)
    tools.init(memory)
    agent = Agent(memory=memory)

    # Restore any prior conversation history (shared with the Telegram bot).
    restored = agent.restore_session()
    if restored:
        print(f"homunculus ready. (restored {restored} messages from previous session)")
    else:
        print("homunculus ready.")
    print("type a task, 'reset' to clear history, or 'exit'.")

    while True:
        try:
            user = input("\nyou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue
        if user in {"exit", "quit"}:
            break
        if user == "reset":
            agent.reset()
            print("(history cleared)")
            continue

        reply = agent.chat(user)
        print(f"\nagent: {reply}")

    # End-of-session reflection: let the agent save anything worth keeping.
    print("\n(reflecting on the session...)")
    print(f"agent: {agent.reflect()}")

    # Persist conversation history so it's available next time (REPL or bot).
    memory.save_session(agent.history)


if __name__ == "__main__":
    main()
