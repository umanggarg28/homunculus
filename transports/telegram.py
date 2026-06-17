"""
Telegram frontend for Homunculus.

This is the third way to talk to Homunculus (alongside REPL and the
heartbeat daemon). The bot:

  1. Long-polls Telegram for incoming messages
  2. Locks access to a single user (TELEGRAM_ALLOWED_USER_ID) — others
     are politely rejected
  3. Routes messages to a persistent Agent so the conversation flows
     across turns (one agent for the whole bot lifetime — restart to
     clear history, or send /reset)
  4. Sends the agent's replies back as Telegram messages

If TELEGRAM_ALLOWED_USER_ID is unset, the bot greets the first message
with the sender's user_id so you know what to paste into .env (no need
for a separate identification bot).

Run:
    docker compose up -d telegram
    docker compose logs -f telegram
    docker compose stop telegram
"""

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.error import Forbidden, InvalidToken, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import events as _events
import threading
import tools
from core import Agent, SYSTEM_PROMPT
from memory import Memory


# Telegram doesn't render markdown tables. It also doesn't render headers
# or any block-level markdown cleanly without parse_mode, and parse_mode
# is brittle (any stray * or _ crashes the send). So we just tell the
# agent to write in a plain-text style. Bullets and short paragraphs.
TELEGRAM_PROMPT_SUFFIX = """

FORMATTING (you're talking via Telegram, a plain-text channel):
- DO NOT use markdown tables. They render as literal pipe characters.
- DO NOT use markdown headers (#, ##). They render as literal hashes.
- DO use plain paragraphs and dash bullets (- like this).
- DO be more concise than usual — phone screens are small.
"""


def _clean_for_plaintext(text: str) -> str:
    """Strip markdown artifacts that don't render as plain text.

    Belt-and-suspenders with the prompt suffix above — LLMs revert to
    standard markdown habits no matter what the prompt says.
    """
    # **bold** and __bold__ → bold (drop the markers)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # `code` → code (drop the backticks; Telegram doesn't render inline code
    # as plain text, the backticks just clutter)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)
    # Leading "# Heading" / "## Heading" lines → "Heading"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Markdown tables → strip pipe-padded rows. Heuristic: if a line is
    # mostly pipes/dashes (header separator), drop it; otherwise replace
    # cell-separators with " · ".
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*\|?[\s\-:\|]+\|?\s*", line) and "|" in line:
            continue  # separator row like `|---|---|`
        if line.count("|") >= 2:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cleaned_lines.append(" · ".join(cells))
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


# Quiet down python-telegram-bot's chatty logging.
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


# We keep a single Agent for the whole bot lifetime. Each message you
# send is one turn in an ongoing conversation. /reset starts fresh.
_agent: Agent | None = None

# When set to a future timestamp, the bot replies with a brief "paused"
# notice instead of routing messages to the agent. Cleared by /resume.
_paused_until_ts: float | None = None


def get_allowed_user_id() -> int | None:
    """Read the allowed user ID from env. Returns None if unset/invalid."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main entry: route a text message to the agent."""
    assert update.effective_user is not None and update.message is not None

    sender_id = update.effective_user.id
    allowed = get_allowed_user_id()

    # Onboarding mode: no allowed-user configured yet. Greet anyone with
    # their own user ID so they can configure the bot.
    if allowed is None:
        await update.message.reply_text(
            f"Hi! Your Telegram user ID is `{sender_id}`.\n\n"
            f"Put this in your .env file:\n"
            f"`TELEGRAM_ALLOWED_USER_ID={sender_id}`\n\n"
            f"Then restart me (`docker compose restart telegram`) "
            f"and I'll start responding as your assistant.",
            parse_mode="Markdown",
        )
        return

    # Locked-down mode: only the configured user can talk to the bot.
    if sender_id != allowed:
        await update.message.reply_text(
            "Access denied. This bot is configured for a single user."
        )
        logging.warning("Rejected message from non-allowed user %d", sender_id)
        return

    user_text = update.message.text or ""
    if not user_text.strip():
        return

    global _agent, _paused_until_ts
    assert _agent is not None, "agent should be set up in main()"

    # Paused? Tell the user (so they know the bot is alive) and bail.
    now_ts = __import__("time").time()
    if _paused_until_ts and now_ts < _paused_until_ts:
        wait_s = int(_paused_until_ts - now_ts)
        wait_human = f"{wait_s}s" if wait_s < 60 else f"{wait_s // 60}m"
        await update.message.reply_text(
            f"(paused, resuming in {wait_human} — send /resume to wake me up)"
        )
        return

    # Drain any notifications sent by other processes (heartbeat, web)
    # since the last user turn. Without this, follow-ups like "explain
    # it" arrive with no context because the heartbeat's notify() runs
    # in a different process and never touches our in-memory history.
    _drain_notifications_into_history(_agent)

    # Optional: show "typing..." while we think.
    await update.message.chat.send_action("typing")

    try:
        reply = _agent.chat(user_text, source="telegram")
    except Exception as e:
        logging.exception("agent.chat raised")
        await update.message.reply_text(f"Error: {type(e).__name__}: {e}")
        return

    # Persist history after every turn so the REPL (or a bot restart) can
    # pick up the thread later. _agent.memory is set in main(); the bot
    # always runs with memory.
    assert _agent.memory is not None
    _agent.memory.save_session(_agent.history)

    # Clean markdown artifacts before sending (LLM ignores prompt rules).
    reply = _clean_for_plaintext(reply)

    # Telegram message limit is 4096 chars. If the agent replied with
    # something longer, send the first chunk and let the user know
    # there's more (rather than silently truncating).
    if len(reply) > 4000:
        truncated_len = len(reply) - 4000
        reply = reply[:4000] + f"\n\n[…truncated {truncated_len} chars · open the web UI for full]"
        logging.info("agent reply truncated by %d chars", truncated_len)
    await update.message.reply_text(reply)


def _drain_notifications_into_history(agent: Agent) -> None:
    """Pull any pending notifications into the agent's in-memory history.

    Notifications are written by `notify()` from any process. Each one
    becomes an `assistant` message in history so the LLM sees "earlier
    I sent you X" and can ground follow-ups against it. The drain is
    idempotent — a persistent pointer in memory/ tracks what's been
    consumed.
    """
    if agent.memory is None:
        return
    try:
        fresh = agent.memory.notifications.drain()
    except Exception as e:
        logging.warning("notification drain failed: %s", e)
        return
    if not fresh:
        return
    for entry in fresh:
        text = entry.get("text", "")
        ts = entry.get("ts")
        try:
            from datetime import datetime
            when = datetime.fromtimestamp(float(ts)).strftime("%H:%M")
        except Exception:
            when = "earlier"
        # Marker prefix tells the LLM this is a notification we
        # proactively sent — not a normal assistant reply mid-conversation.
        agent.history.append({
            "role": "assistant",
            "content": f"[notification I sent you at {when}]\n\n{text}",
        })
    logging.info("drained %d pending notification(s) into history", len(fresh))


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe the agent's conversation history (keeps long-term memory)."""
    assert update.message is not None
    global _agent
    if _agent is not None:
        _agent.reset()
    await update.message.reply_text("History cleared. Memory still intact.")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pause the bot for N minutes (default 60).

    Usage: `/pause 30` to pause for 30 minutes, or `/pause` for 1 hour.
    Useful when the agent is misbehaving and you need it silenced
    without restarting the container.
    """
    assert update.message is not None
    global _paused_until_ts
    args = (update.message.text or "").split()
    minutes = 60
    if len(args) > 1:
        try:
            minutes = max(1, min(24 * 60, int(args[1])))
        except ValueError:
            await update.message.reply_text("Usage: /pause [minutes] (default 60, max 1440)")
            return
    import time as _time
    _paused_until_ts = _time.time() + minutes * 60
    await update.message.reply_text(
        f"Paused for {minutes} minute(s). /resume to wake me up."
    )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume the bot before the pause expires."""
    assert update.message is not None
    global _paused_until_ts
    _paused_until_ts = None
    await update.message.reply_text("Awake.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /start with a brief intro."""
    assert update.message is not None
    await update.message.reply_text(
        "I'm Homunculus, your personal AI assistant. Send me a task, "
        "ask me a question, or just chat. /reset to clear history."
    )


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set in .env")

    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set in .env")

    # Set up memory and the persistent agent.
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    memory = Memory(memory_dir)
    # autonomous=True disables shell_exec — the bot has no terminal, so a
    # y/N prompt would hang the daemon. The agent can still read/write
    # files, remember, and notify (the only tools needed for chat).
    tools.init(memory, autonomous=True)

    global _agent
    _agent = Agent(memory=memory, system_prompt=SYSTEM_PROMPT + TELEGRAM_PROMPT_SUFFIX)
    restored = _agent.restore_session()
    if restored:
        logging.info("Restored %d messages from previous session", restored)

    allowed = get_allowed_user_id()
    if allowed is None:
        logging.warning(
            "TELEGRAM_ALLOWED_USER_ID is not set. The bot will run in "
            "ONBOARDING mode and reply to any sender with their user ID."
        )
    else:
        logging.info("Bot locked to user_id=%d", allowed)

    def _ping_loop() -> None:
        while True:
            try:
                _events.emit("service_ping", name="telegram", text="alive")
            except Exception:
                pass
            time.sleep(600)

    threading.Thread(target=_ping_loop, daemon=True).start()

    def _build_app() -> Application:
        # Fresh Application per attempt — a failed initialize leaves the old
        # one in a partial state that can't be re-run.
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("reset", reset_command))
        app.add_handler(CommandHandler("pause", pause_command))
        app.add_handler(CommandHandler("resume", resume_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        return app

    logging.info("Telegram bot starting (long-polling)...")
    run_polling_with_retry(_build_app)


def _fresh_event_loop() -> None:
    """Install a new asyncio event loop for the next polling attempt.

    run_polling() runs the bot on the current loop and CLOSES it on exit, so a
    retry on the old loop raises 'Event loop is closed'. A fresh loop per
    attempt lets the listener actually reconnect, not just avoid crashing."""
    asyncio.set_event_loop(asyncio.new_event_loop())


def run_polling_with_retry(build_app, *, base_backoff: int = 30, max_backoff: int = 600,
                           sleep=time.sleep, reset_loop=_fresh_event_loop) -> None:
    """Run the bot, surviving an unreachable Telegram.

    ``run_polling()`` initializes by calling ``get_me()``, which times out when
    api.telegram.org is unreachable (e.g. a regional block). Crashing here would
    make Docker restart the container in a tight loop. Instead we wait and retry
    with exponential backoff so the listener stays alive and reconnects when
    Telegram is reachable again. Outbound notifications are unaffected:
    tools.notify degrades to Discord + the web feed independently of this.
    """
    backoff = base_backoff
    while True:
        reset_loop()  # fresh loop each attempt — run_polling() closes the loop on exit
        app = build_app()
        try:
            app.run_polling()
            return  # clean shutdown (SIGTERM) — don't loop
        except (InvalidToken, Forbidden):
            # Misconfiguration, not a transient outage — retrying forever would
            # just hide it. Fail loudly so the operator fixes the token/perms.
            logging.exception("Telegram rejected the bot credentials — not retrying")
            raise
        except (TimedOut, NetworkError) as e:
            logging.warning(
                "Telegram unreachable (%s). Retrying in %ds — Discord and the "
                "web feed are unaffected.", e, backoff,
            )
        except Exception:
            # Unexpected, but treat as transient: stay alive and retry rather
            # than crash-loop. logging.exception keeps it visible in the logs.
            logging.exception("Telegram polling failed; retrying in %ds", backoff)
        sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    main()
