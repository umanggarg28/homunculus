"""Force a daily REFLECTION tick right now (ops + test tool).

Mirrors the reflection branch of heartbeat.tick() exactly: same prompt,
same fresh Agent, same source tag, same tools. Use it to exercise and
verify the reflection — including the delivery quality self-critique
(STEP 1d) — on demand instead of waiting for the once-a-day schedule.

It does NOT mark the reflection as done for the calendar day, so the
scheduled reflection still runs normally.

    docker compose exec web sh -c 'cd /app && uv run python scripts/run_reflection_now.py'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools  # noqa: E402
from core import Agent  # noqa: E402
from heartbeat import (  # noqa: E402
    REFLECTION_PROMPT_TEMPLATE,
    _format_recent_deliveries,
    _today_str,
    _yesterday_iso_and_path,
)
from memory import Memory  # noqa: E402
from tasks import TaskStore  # noqa: E402


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    tasks_dir = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    model = os.environ.get("HOMUNCULUS_MODEL_HEARTBEAT", "openai/gpt-oss-120b")

    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)
    agent = Agent(memory=memory, model=model)

    today = _today_str()
    yesterday_iso, yesterday_path = _yesterday_iso_and_path()
    prompt = REFLECTION_PROMPT_TEMPLATE.format(
        today=today,
        yesterday=yesterday_iso,
        yesterday_path=yesterday_path,
        recent_deliveries=_format_recent_deliveries(TaskStore(tasks_dir)),
    )
    print(
        f"[reflection] today={today} reviewing={yesterday_iso} model={agent.model}",
        flush=True,
    )
    response = agent.chat(prompt, source="heartbeat")
    print("\n=== reflection reply ===\n" + (response or "(no reply)"), flush=True)


if __name__ == "__main__":
    main()
