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
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import tools
from core import Agent
from memory import Memory


HEARTBEAT_PROMPT_TEMPLATE = """It's a scheduled heartbeat tick — no user is
talking to you right now. The current time is {now_iso}.

Look at your MEMORY INDEX (already in your system prompt) and decide if
there's anything proactive worth doing RIGHT NOW.

Examples of useful proactive actions:
- Notice a follow-up the user mentioned and leave a `remember()` note
  about it.
- Draft a short summary of recent work and save it (a simple filename
  like `summary.md` lands in the workspace).
- For time-sensitive memories (e.g. a deadline tomorrow), use `notify()`
  to push a message to the user's Telegram. Use sparingly.

Scheduling: by default the next tick is in ~10 minutes. If you'd like
to adjust that (e.g. wake at 8am tomorrow before a deadline, or in
2 hours to check progress), call `schedule_next_tick("YYYY-MM-DDTHH:MM:SS")`.
Must be in the future, within 24h.

Important rules:
- DO NOT read the daily log files unless you have a specific recall
  task. Logs contain your own previous heartbeat output and reading
  them every tick creates a feedback loop. Trust your memory index.
- If nothing genuinely useful comes to mind, say so in ONE line and
  STOP. Doing nothing is fine. Don't invent busywork.
- shell_exec is disabled. If a task would need shell access, call
  remember() to leave a note for the user.
"""


REFLECTION_PROMPT_TEMPLATE = """It's a daily REFLECTION tick — no user is
talking to you right now. The current date is {today}.

Your task: review what happened yesterday ({yesterday}) and learn from it.

Step 1 — Read yesterday's log file:
    read_file("memory/logs/{yesterday_path}.md")
(If the file doesn't exist, yesterday was quiet — say so in one line and stop.)

Step 2 — Look for PATTERNS worth carrying forward:
- Did the user correct you on something? → save as a "feedback" memory.
- Did the user reveal an ongoing goal, deadline, or project state? →
  save as a "project" memory.
- Did you make a mistake (wrong tool, wrong assumption) that a future
  you should avoid? → save as a "feedback" memory phrased as a rule.
- Did the user confirm a non-obvious choice worked well? → save as a
  "feedback" memory so future-you keeps doing it.

Step 3 — Save AT MOST 3 new memories via remember(). Fewer is fine.
Skip anything trivial or already covered by an existing memory in your
index. Quality over quantity.

Step 4 — Reply with a ONE-LINE summary of what you learned (or
"nothing notable from yesterday"). Then stop.

Important:
- Reading yesterday's log is the ONLY log read you should do this tick.
  Don't chain into older logs.
- Don't write to workspace files. This tick is for memory only.
- Don't call notify(). Reflections are silent.
- shell_exec is disabled.
"""


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday_iso_and_path() -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY/MM/YYYY-MM-DD) for yesterday."""
    y = datetime.now() - timedelta(days=1)
    iso = y.strftime("%Y-%m-%d")
    path_form = y.strftime("%Y/%m/%Y-%m-%d")
    return iso, path_form


def tick(memory: Memory, model: str | None) -> None:
    """One heartbeat iteration — fresh agent, one prompt, then discard.

    If reflection is due (we haven't reflected today yet) AND there's a
    yesterday to look at, this tick runs the reflection prompt instead
    of the normal proactive prompt. The marker is updated on success so
    we don't reflect twice in the same calendar day.
    """
    today = _today_str()
    last = memory.get_last_reflection_date()
    do_reflection = last is None or last < today

    agent = Agent(memory=memory, model=model)
    now_iso = datetime.now().isoformat(timespec="seconds")

    if do_reflection:
        yesterday_iso, yesterday_path = _yesterday_iso_and_path()
        print(f"\n[heartbeat] REFLECTION tick at {now_iso} "
              f"(reviewing {yesterday_iso}, model={agent.model})", flush=True)
        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            today=today,
            yesterday=yesterday_iso,
            yesterday_path=yesterday_path,
        )
        response = agent.chat(prompt)
        memory.set_last_reflection_date(today)
        print(f"[agent] {response}", flush=True)
        return

    print(f"\n[heartbeat] tick at {now_iso} (model={agent.model})", flush=True)
    prompt = HEARTBEAT_PROMPT_TEMPLATE.format(now_iso=now_iso)
    response = agent.chat(prompt)
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

    default_interval = interval_min * 60
    while True:
        try:
            tick(memory, model=model)
        except Exception:
            # Don't let one bad tick kill the daemon. Log and continue.
            print("[heartbeat] error during tick:", flush=True)
            traceback.print_exc()

        sleep_seconds = _compute_sleep(memory, default_interval)
        wake_at = (datetime.now() + timedelta(seconds=sleep_seconds)).isoformat(timespec="seconds")
        print(f"[heartbeat] sleeping {sleep_seconds:.0f}s, next tick ~{wake_at}", flush=True)
        time.sleep(sleep_seconds)


def _compute_sleep(memory: Memory, default_seconds: float) -> float:
    """Read the agent's self-scheduled wake time, or fall back to default.

    Pops the scheduled time so each tick decides independently — if the
    agent forgets to schedule next time, we don't reuse a stale value.
    """
    scheduled = memory.pop_next_tick()
    if scheduled is None:
        return default_seconds
    try:
        target = datetime.fromisoformat(scheduled)
    except ValueError:
        print(f"[heartbeat] could not parse scheduled time '{scheduled}', using default", flush=True)
        return default_seconds
    delta = (target - datetime.now()).total_seconds()
    if delta <= 0:
        print(f"[heartbeat] scheduled time {scheduled} is in the past, using default", flush=True)
        return default_seconds
    # The schedule_next_tick tool already caps at 24h on the way in, but
    # double-check here as a defense-in-depth.
    capped = min(delta, 24 * 3600)
    if capped < delta:
        print(f"[heartbeat] capping {delta:.0f}s schedule to 24h", flush=True)
    return capped


if __name__ == "__main__":
    main()
