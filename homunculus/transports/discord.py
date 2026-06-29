"""
Discord frontend for Homunculus.

A parallel chat channel alongside Telegram. Like the Telegram bot, it:

  1. Connects to the Discord gateway and listens for messages
  2. Locks to a single user (DISCORD_ALLOWED_USER_ID) — others are ignored
  3. Routes messages to a persistent Agent (one agent for the bot lifetime)
  4. Sends replies back, and drains notifications other processes queued

Discord is a relay channel: it needs only outbound internet (Discord's servers
push to the user's device), so it works from a purely-local deploy with no
inbound reachability/tunnel — unlike Web Push. Outgoing pushes (briefs/quiz) go
through tools.notify's Discord sender; this file handles the incoming side
(replies, e.g. quiz answers).

Setup (one-time):
  1. https://discord.com/developers/applications → New Application → Bot
  2. Enable "MESSAGE CONTENT INTENT" under the Bot tab.
  3. Copy the bot token → DISCORD_BOT_TOKEN in .env
  4. OAuth2 → URL Generator → scopes: bot; perms: Send Messages, Read Message
     History → open the URL, add the bot to a server (or use a DM).
  5. Send it a message; it replies with your user ID + the channel ID to set as
     DISCORD_ALLOWED_USER_ID and DISCORD_CHANNEL_ID, then restart.

Run:
    docker compose up -d discord
    docker compose logs -f discord
"""

import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import discord
from dotenv import load_dotenv

import homunculus.events as _events
from homunculus import REPO_ROOT
from homunculus import tools
from homunculus.approvals import try_resolve_from_chat
from homunculus.core import Agent, SYSTEM_PROMPT
from homunculus.memory import Memory
from homunculus.logging_config import configure_logging


DISCORD_PROMPT_SUFFIX = """

FORMATTING (you're talking via Discord):
- Discord renders **bold**, *italic*, `code`, and ```code blocks``` — those are fine.
- It does NOT render [label](url) links in normal messages — paste bare URLs instead.
- No markdown tables or # headers; use dash bullets and short paragraphs.
- Keep replies concise — phone screens are small. 2000-char hard limit per message.
"""

configure_logging()


def _allowed_user_id() -> int | None:
    raw = os.environ.get("DISCORD_ALLOWED_USER_ID", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _flatten_links(text: str) -> str:
    """Discord plain messages don't render [label](url) — flatten to 'label: url'
    so the URL stays clickable (Discord auto-embeds bare URLs)."""
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)


def _drain_notifications_into_history(agent: Agent) -> None:
    """Pull notifications other processes queued (heartbeat brief/quiz) into the
    agent's history so a reply like 'the answer is X' has context. Mirrors the
    Telegram bot. Idempotent via the persistent drain pointer."""
    if agent.memory is None:
        return
    try:
        fresh = agent.memory.notifications.drain()
    except Exception as e:
        logging.warning("notification drain failed: %s", e)
        return
    for entry in fresh:
        text = entry.get("text", "")
        try:
            when = datetime.fromtimestamp(float(entry.get("ts") or 0)).strftime("%H:%M")
        except Exception:
            when = "earlier"
        agent.history.append({
            "role": "assistant",
            "content": f"[notification I sent you at {when}]\n\n{text}",
        })
    if fresh:
        logging.info("drained %d pending notification(s) into history", len(fresh))


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("DISCORD_BOT_TOKEN is not set in .env")
    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set in .env")

    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)

    agent = Agent(memory=memory, system_prompt=SYSTEM_PROMPT + DISCORD_PROMPT_SUFFIX)
    restored = agent.restore_session()
    if restored:
        logging.info("Restored %d messages from previous session", restored)

    allowed = _allowed_user_id()
    if allowed is None:
        logging.warning(
            "DISCORD_ALLOWED_USER_ID unset — ONBOARDING mode: the bot replies "
            "to any sender with their user ID + channel ID to configure."
        )
    else:
        logging.info("Discord bot locked to user_id=%d", allowed)

    intents = discord.Intents.default()
    intents.message_content = True  # also enable "Message Content Intent" in the bot settings
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        logging.info("Discord bot connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message) -> None:
        # Never react to our own messages (would loop).
        if message.author == client.user:
            return
        user_text = (message.content or "").strip()
        if not user_text:
            return

        # Onboarding: no allowed user yet — hand back the IDs to configure.
        if _allowed_user_id() is None:
            await message.channel.send(
                f"Hi! Your Discord user ID is `{message.author.id}` and this "
                f"channel ID is `{message.channel.id}`.\n"
                f"Set in .env:\n`DISCORD_ALLOWED_USER_ID={message.author.id}`\n"
                f"`DISCORD_CHANNEL_ID={message.channel.id}`\n"
                f"then `docker compose restart discord`."
            )
            return

        if message.author.id != _allowed_user_id():
            return  # silently ignore others (single-user assistant)

        # Owner-gated proposal command ("approve prop-0021" / "reject prop-0021
        # <reason>"). Resolved directly by the shared resolver — no LLM, no
        # budget. Safe here: only the configured user reaches this point.
        approval_reply = try_resolve_from_chat(user_text)
        if approval_reply is not None:
            await message.channel.send(approval_reply)
            return

        _drain_notifications_into_history(agent)
        try:
            async with message.channel.typing():
                # agent.chat is sync/blocking; run it off the event loop so the
                # gateway heartbeat isn't starved.
                import asyncio
                reply = await asyncio.to_thread(agent.chat, user_text, source="discord")
        except Exception as e:
            logging.exception("agent.chat raised")
            await message.channel.send(f"Error: {type(e).__name__}: {e}")
            return

        if agent.memory is not None:
            agent.memory.save_session(agent.history)

        reply = _flatten_links(reply)
        # Discord's hard limit is 2000 chars/message — chunk rather than truncate.
        for i in range(0, len(reply), 1990):
            await message.channel.send(reply[i:i + 1990])

    def _ping_loop() -> None:
        import time
        while True:
            try:
                _events.emit("service_ping", name="discord", text="alive")
            except Exception:
                pass
            time.sleep(600)

    threading.Thread(target=_ping_loop, daemon=True).start()

    logging.info("Discord bot starting (gateway)...")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
