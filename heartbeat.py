"""
Homunculus heartbeat — autonomous self-prompting loop.

Wakes every HEARTBEAT_INTERVAL_MINUTES minutes, runs ONE fresh agent
session with the heartbeat prompt, then sleeps. The agent can read its
memory, write workspace files, and remember new facts on its own.
shell_exec is disabled (see tools.py — autonomous=True).

This is what makes Homunculus "autonomous" instead of just "a chatbot":
it acts even when no human is talking to it.

Run:
    docker compose up -d heartbeat        # background, auto-restart
    docker compose logs -f heartbeat      # watch what it's doing
    docker compose stop heartbeat         # stop it
"""

import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

import tools
from core import Agent
from memory import Memory


HEARTBEAT_PROMPT = """It's a scheduled heartbeat tick — no user is talking
to you right now. Look at your MEMORY INDEX (already in your system
prompt) and decide if there's anything proactive worth doing RIGHT NOW.

Examples of useful proactive actions:
- Notice a follow-up the user mentioned and leave a `remember()` note
  about it.
- Draft a short summary of recent work and save it (a simple filename
  like `summary.md` lands in the workspace).

Important rules:
- DO NOT read the daily log files unless you have a specific recall
  task. Logs contain your own previous heartbeat output and reading
  them every tick creates a feedback loop. Trust your memory index.
- If nothing genuinely useful comes to mind, say so in ONE line and
  STOP. Doing nothing is fine. Don't invent busywork.
- shell_exec is disabled. If a task would need shell access, call
  remember() to leave a note for the user.
"""


def tick(memory: Memory, model: str | None) -> None:
    """One heartbeat iteration — fresh agent, one prompt, then discard."""
    agent = Agent(memory=memory, model=model)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[heartbeat] tick at {timestamp} (model={agent.model})", flush=True)
    response = agent.chat(HEARTBEAT_PROMPT)
    print(f"[agent] {response}", flush=True)


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")
    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set.")

    interval_min = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "10"))
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    # Heartbeat's task is simpler than the bot/REPL — pick a smaller
    # default. Saves ~6x on tokens-per-tick. Override via env if needed.
    model = os.environ.get("HOMUNCULUS_MODEL_HEARTBEAT", "openai/gpt-oss-20b")

    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)

    print(f"[heartbeat] starting, interval = {interval_min} min, model = {model}", flush=True)

    while True:
        try:
            tick(memory, model=model)
        except Exception:
            # Don't let one bad tick kill the daemon. Log and continue.
            print("[heartbeat] error during tick:", flush=True)
            traceback.print_exc()
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
