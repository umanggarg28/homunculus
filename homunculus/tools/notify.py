"""Notification tools: notify (push to user's Telegram).

Hardening notes:
  • Markdown→Telegram HTML uses markdown-it-py instead of hand-rolled
    regex, which handles `[array[0]](url)`, nested fences, and partial
    inline emphasis correctly. If the converted HTML still trips
    Telegram's parser the function falls back to plain text.
  • Per-process rate limit (default 5/min) prevents a runaway agent
    from spamming the user's phone and getting the bot blocked.
  • notify() queues the sent text to `_notifications.jsonl` so the
    Telegram bot can ground subsequent user follow-ups against it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from threading import Lock

import httpx

try:
    from markdown_it import MarkdownIt  # type: ignore
    _MD: MarkdownIt | None = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("strikethrough")
except Exception:  # pragma: no cover — fallback if dep missing in container
    _MD = None


# Rate-limit: at most NOTIFY_MAX_PER_WINDOW calls per NOTIFY_WINDOW_S
# seconds, sliding window. Tunable via env. Protects the user from a
# bug-induced storm of pushes.
NOTIFY_MAX_PER_WINDOW = int(os.environ.get("HOMUNCULUS_NOTIFY_MAX_PER_MIN", "5"))
NOTIFY_WINDOW_S = float(os.environ.get("HOMUNCULUS_NOTIFY_WINDOW_S", "60"))

# Idempotency: re-sending the exact same text within this window is suppressed,
# so a retry storm, a heartbeat restart mid-run, or a skill that re-calls notify
# on a partial success can never produce duplicate user messages. The guarantee
# is deterministic — it does not depend on the model reading the return value
# correctly. Set to 0 to disable. Two genuinely-identical messages minutes apart
# essentially only happen from a bug or retry, so a few minutes is safe.
NOTIFY_DEDUP_WINDOW_S = float(os.environ.get("HOMUNCULUS_NOTIFY_DEDUP_WINDOW_S", "300"))

# Telegram-supported HTML tags. Anything else we generate gets stripped
# before send so a stray `<table>` from a code block doesn't 400 the API.
_TELEGRAM_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler"}

_send_history: deque[float] = deque(maxlen=NOTIFY_MAX_PER_WINDOW * 4)
_send_lock = Lock()


def _rate_limit_check() -> str | None:
    """Return an error string if rate-limited; None otherwise. Records
    the attempt on success so subsequent calls within the window see it.
    """
    now = time.time()
    with _send_lock:
        cutoff = now - NOTIFY_WINDOW_S
        while _send_history and _send_history[0] < cutoff:
            _send_history.popleft()
        if len(_send_history) >= NOTIFY_MAX_PER_WINDOW:
            oldest = _send_history[0]
            wait = NOTIFY_WINDOW_S - (now - oldest)
            return (
                f"ERROR: notify() rate-limited ({NOTIFY_MAX_PER_WINDOW} per "
                f"{NOTIFY_WINDOW_S:.0f}s). Retry in {wait:.0f}s. If this is "
                f"intentional, write to a file or memory instead."
            )
        _send_history.append(now)
        return None


def notify(text: str, preview: bool = False) -> str:
    """Push a proactive Telegram message. Interrupts the user — use sparingly.

    If `preview=True`, returns the formatted message that *would* be
    sent without actually sending. Useful when the agent wants to
    show the user what it's about to push and ask for confirmation
    on a single round-trip (the user can then say "send it" and the
    agent re-calls notify without preview).
    """
    if not text or not text.strip():
        return "ERROR: notify() requires non-empty text."

    if preview:
        body = _markdown_to_telegram_html(text)
        return (
            f"[PREVIEW · not sent] would push {len(text)} chars:\n"
            f"---\n{body}\n---\n"
            f"Call notify(text) again without preview to actually send."
        )

    rate_err = _rate_limit_check()
    if rate_err:
        return rate_err

    result = deliver(text)
    return _format_delivery(text, result)


def deliver(text: str) -> dict:
    """Record the message to the web feed (always-on channel) and fan it out to
    every configured push channel (Telegram, Discord, …). Shared by notify() and
    the heartbeat's autonomous fallback.

    The web app is treated as a guaranteed channel: we record FIRST so a delivery
    is never lost even when every push channel is unavailable. Push channels are
    best-effort on top. Returns a dict:
    {recorded: bool, delivered: [names], failed: [(name, err)], duplicate: bool}.

    Idempotent: an exact-text resend within NOTIFY_DEDUP_WINDOW_S is suppressed
    (duplicate=True) and reaches no channel, so the user never gets the same
    message twice no matter how many times this is called.
    """
    if _recently_delivered(text):
        return {"recorded": False, "delivered": [], "failed": [], "duplicate": True}
    recorded = _record_to_feed(text)
    delivered: list[str] = []
    failed: list[tuple[str, str]] = []
    for name, send in _channel_senders():
        try:
            err = send(text)
        except Exception as e:  # a channel must never crash the others
            err = f"{type(e).__name__}: {e}"
        if err:
            failed.append((name, err))
        else:
            delivered.append(name)
    if failed:
        # Raw transport errors are diagnostic, not actionable by the agent.
        # They live in the logs so they don't leak into the agent-facing
        # return, where words like "failed"/"timed out" read as "retry me".
        logging.warning(
            "notify: delivered via %s; channel(s) unreachable: %s",
            delivered or ["web feed only"],
            "; ".join(f"{nm} ({err})" for nm, err in failed),
        )
    return {"recorded": recorded, "delivered": delivered, "failed": failed, "duplicate": False}


def _recently_delivered(text: str) -> bool:
    """True if this exact text was delivered within NOTIFY_DEDUP_WINDOW_S. Reads
    the persistent feed log (every delivery records there), so the dedup holds
    across separate notify() calls in one run and across a process restart. On
    any read error it returns False — failing open, so a dedup-store hiccup never
    silently swallows a real notification."""
    if NOTIFY_DEDUP_WINDOW_S <= 0:
        return False
    try:
        memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
        queue_path = memory_dir / "_notifications.jsonl"
        if not queue_path.exists():
            return False
        cutoff = time.time() - NOTIFY_DEDUP_WINDOW_S
        # Notifications are infrequent; scanning the tail is cheap and bounds work.
        for line in queue_path.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("text") == text and entry.get("ts", 0) >= cutoff:
                return True
    except OSError:
        return False
    return False


def _channel_senders() -> list[tuple[str, Callable[[str], str | None]]]:
    """Configured push channels, in order. Additive: a channel is included only
    when its credentials are present, so enabling or disabling a channel needs no
    code change — just env vars."""
    senders: list[tuple[str, Callable[[str], str | None]]] = []
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_ALLOWED_USER_ID"):
        senders.append(("telegram", _send_to_telegram))
    if os.environ.get("DISCORD_BOT_TOKEN") and os.environ.get("DISCORD_CHANNEL_ID"):
        senders.append(("discord", _send_to_discord))
    return senders


def _format_delivery(text: str, result: dict) -> str:
    """The agent-facing result. The web app feed is the always-on channel, so a
    message that reaches the feed (or any push channel) IS delivered — and the
    return must read that way to a weak model, or it resends the same message
    over and over. So we (a) lead with an unambiguous verdict and an explicit
    "do not resend", and (b) keep raw transport errors out of the string: words
    like "failed"/"timed out"/"ERROR" from a best-effort channel read as "the
    call failed, retry me". Those go to the logs instead (see deliver()). A hard
    ERROR is returned only when the message reached nothing at all."""
    if result.get("duplicate"):
        return (
            "ALREADY DELIVERED — this exact message was just sent, so this call "
            "was suppressed as a duplicate. The user has it. Do not resend."
        )
    if not (result["recorded"] or result["delivered"]):
        return "ERROR: notify could not record or deliver the message anywhere."

    reached: list[str] = []
    if result["recorded"]:
        reached.append("web app feed")
    reached.extend(result["delivered"])
    summary = f"DELIVERED to the user via {', '.join(reached)}. Do not resend."

    if result["failed"]:
        skipped = ", ".join(nm for nm, _ in result["failed"])
        summary += (
            f" ({skipped} unreachable and skipped — expected, no action needed; "
            f"the user already has the message.)"
        )
    return summary


# Transient send failures (timeout, connect reset, DNS hiccup) are retried with
# short backoff so one blip doesn't discard a delivery; only a persistent failure
# surfaces as an error. Zero LLM cost.
NOTIFY_SEND_ATTEMPTS = int(os.environ.get("HOMUNCULUS_NOTIFY_SEND_ATTEMPTS", "3"))
_NOTIFY_BACKOFF_S = (1.0, 2.5)  # waited before attempt 2, attempt 3
# Short connect cap on purpose: when a channel host is unreachable, TCP connect
# hangs, and a default dual-stack connect (~20s) × retries would exceed the 60s
# tool-execution wrapper and stall the agent loop. A 5s connect cap keeps three
# attempts + backoff to ~18s, so notify fails fast and the agent can record a
# clean failure instead of grinding.
_NOTIFY_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


def _post_with_retry(
    url: str, json_payload: dict, headers: dict | None = None
) -> tuple[httpx.Response | None, str | None]:
    """POST JSON, retrying transient transport errors (timeout, connect reset)
    with short backoff. Returns (response, None) once a request completes (any
    HTTP status), or (None, error) if every attempt hit a transport error. Used
    by all channel senders so retry/timeout policy lives in one place."""
    last_err = ""
    for attempt in range(NOTIFY_SEND_ATTEMPTS):
        try:
            return httpx.post(url, json=json_payload, headers=headers or {}, timeout=_NOTIFY_TIMEOUT), None
        except httpx.HTTPError as e:
            last_err = str(e) or type(e).__name__
            if attempt < NOTIFY_SEND_ATTEMPTS - 1:
                time.sleep(_NOTIFY_BACKOFF_S[min(attempt, len(_NOTIFY_BACKOFF_S) - 1)])
    return None, last_err


def _post_telegram(token: str, payload: dict) -> tuple[httpx.Response | None, str | None]:
    """Telegram sendMessage with retry (delegates to _post_with_retry)."""
    return _post_with_retry(f"https://api.telegram.org/bot{token}/sendMessage", payload)


def _send_to_telegram(text: str) -> str | None:
    """Send text to Telegram. Returns an error string on failure, None on success."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        return None  # silently skip if not configured (e.g. during tests)

    body = _markdown_to_telegram_html(text)
    response, err = _post_telegram(
        token,
        {"chat_id": chat_id, "text": body, "parse_mode": "HTML", "disable_web_page_preview": True},
    )
    if err is not None or response is None:
        return f"ERROR: Telegram request failed after {NOTIFY_SEND_ATTEMPTS} attempts: {err}"

    if response.status_code != 200:
        plain = _strip_markdown(text)
        fallback, ferr = _post_telegram(
            token,
            {"chat_id": chat_id, "text": plain, "disable_web_page_preview": True},
        )
        if ferr is not None or fallback is None:
            return f"ERROR: Telegram HTML send failed ({response.status_code}) and fallback also failed: {ferr}"
        if fallback.status_code != 200:
            return f"ERROR: Telegram send failed ({response.status_code}); fallback {fallback.status_code}"

    # NOTE: feed recording is done once in deliver(), not here — sending to
    # multiple channels must not write the same notification N times.
    return None


def _send_to_discord(text: str) -> str | None:
    """Send text to a Discord channel via the bot REST API. Returns an error
    string on failure, None on success. Discord relays to the user's phone, so
    this needs only outbound internet — no public URL/tunnel (unlike Web Push).

    Discord renders **bold**/*italic*/`code` natively but NOT [label](url) links
    in plain messages, so links are flattened to 'label: url' (Discord auto-embeds
    bare URLs). 2000-char message cap is enforced."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID")
    if not token or not channel_id:
        return None  # not configured — skip silently (same as Telegram)

    body = _markdown_to_discord(text)
    if len(body) > 2000:
        body = body[:1997] + "..."
    resp, err = _post_with_retry(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        json_payload={"content": body},
        headers={"Authorization": f"Bot {token}"},
    )
    if err is not None or resp is None:
        return f"ERROR: Discord request failed after {NOTIFY_SEND_ATTEMPTS} attempts: {err}"
    if resp.status_code not in (200, 201):
        return f"ERROR: Discord send failed (HTTP {resp.status_code}): {resp.text[:200]}"
    return None


def _markdown_to_discord(text: str) -> str:
    """Discord supports most markdown natively; only [label](url) links don't
    render in plain messages. Flatten those to 'label: url' so links stay usable
    (Discord auto-links the bare URL)."""
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)


def _record_to_feed(text: str) -> bool:
    """Append the message to memory/_notifications.jsonl — the always-on web
    channel. The web transmissions feed shows it and the Telegram/Discord bots
    drain it into chat history. Best-effort; returns True on success."""
    try:
        memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
        memory_dir.mkdir(parents=True, exist_ok=True)
        queue_path = memory_dir / "_notifications.jsonl"
        entry = {"ts": time.time(), "text": text}
        with queue_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        logging.warning("[notify] could not record to feed: %s", e)
        return False


# Back-compat alias — some callers/tests import the old name.
_queue_for_telegram_history = _record_to_feed


# ── markdown → telegram HTML ────────────────────────────────────────


def _markdown_to_telegram_html(text: str) -> str:
    """Convert the agent's markdown output into the Telegram HTML subset.

    Uses markdown-it-py when available (handles edge cases the previous
    regex pass failed on: links with `]` in the label, nested fences,
    asterisks inside URLs). Falls back to the regex converter if the
    library is missing or the parser raises.
    """
    if _MD is not None:
        try:
            return _md_to_tg_via_parser(text)
        except Exception:
            pass  # fall through to regex
    return _md_to_tg_via_regex(text)


def _md_to_tg_via_parser(text: str) -> str:
    """Walk markdown-it tokens and emit Telegram HTML.

    Telegram's subset: <b>, <i>, <u>, <s>, <code>, <pre>, <a>,
    <tg-spoiler>. Block-level structure (lists, paragraphs, headings)
    gets flattened to newlines + bullet markers since Telegram doesn't
    render block HTML."""
    assert _MD is not None
    tokens = _MD.parse(text)
    out: list[str] = []

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def walk(toks, in_list_kind: str | None = None) -> None:
        # Counter for ordered-list numbering. Resets at each list_open.
        ol_counter = [0]
        i = 0
        while i < len(toks):
            t = toks[i]
            tt = t.type

            if tt == "paragraph_open":
                # find matching close
                depth = 1
                inner: list = []
                j = i + 1
                while j < len(toks) and depth:
                    if toks[j].type == "paragraph_open":
                        depth += 1
                    elif toks[j].type == "paragraph_close":
                        depth -= 1
                        if depth == 0:
                            break
                    inner.append(toks[j])
                    j += 1
                walk(inner)
                out.append("\n\n")
                i = j + 1
                continue

            if tt == "heading_open":
                # treat heading as bold inline + newline
                depth = 1
                inner = []
                j = i + 1
                while j < len(toks) and depth:
                    if toks[j].type == "heading_open":
                        depth += 1
                    elif toks[j].type == "heading_close":
                        depth -= 1
                        if depth == 0:
                            break
                    inner.append(toks[j])
                    j += 1
                out.append("<b>")
                walk(inner)
                out.append("</b>\n\n")
                i = j + 1
                continue

            if tt in ("bullet_list_open", "ordered_list_open"):
                kind = "ul" if tt == "bullet_list_open" else "ol"
                depth = 1
                inner = []
                j = i + 1
                close_kind = f"{'bullet' if kind == 'ul' else 'ordered'}_list_close"
                while j < len(toks) and depth:
                    if toks[j].type == tt:
                        depth += 1
                    elif toks[j].type == close_kind:
                        depth -= 1
                        if depth == 0:
                            break
                    inner.append(toks[j])
                    j += 1
                walk(inner, in_list_kind=kind)
                out.append("\n")
                i = j + 1
                continue

            if tt == "list_item_open":
                depth = 1
                inner = []
                j = i + 1
                while j < len(toks) and depth:
                    if toks[j].type == "list_item_open":
                        depth += 1
                    elif toks[j].type == "list_item_close":
                        depth -= 1
                        if depth == 0:
                            break
                    inner.append(toks[j])
                    j += 1
                if in_list_kind == "ol":
                    ol_counter[0] += 1
                    out.append(f"{ol_counter[0]}. ")
                else:
                    out.append("• ")
                walk(inner)
                out.append("\n")
                i = j + 1
                continue

            if tt == "fence" or tt == "code_block":
                out.append(f"<pre>{esc(t.content.rstrip())}</pre>\n")
                i += 1
                continue

            if tt == "inline":
                _render_inline(t.children or [], out, esc)
                i += 1
                continue

            if tt == "softbreak" or tt == "hardbreak":
                out.append("\n")
                i += 1
                continue

            if tt == "hr":
                out.append("───────\n")
                i += 1
                continue

            # Default: skip unknown openers/closers, render their content
            # if present.
            if t.content:
                out.append(esc(t.content))
            i += 1

    walk(tokens)
    # Collapse 3+ newlines into 2 (no triple-blank paragraphs).
    flat = "".join(out)
    flat = re.sub(r"\n{3,}", "\n\n", flat).strip()
    return flat


def _render_inline(children, out: list[str], esc) -> None:
    """Render the children of a markdown-it `inline` token."""
    stack: list[str] = []
    for c in children:
        ct = c.type
        if ct == "text":
            out.append(esc(c.content))
        elif ct == "softbreak" or ct == "hardbreak":
            out.append("\n")
        elif ct == "code_inline":
            out.append(f"<code>{esc(c.content)}</code>")
        elif ct == "strong_open":
            out.append("<b>")
            stack.append("</b>")
        elif ct == "strong_close":
            out.append(stack.pop() if stack and stack[-1] == "</b>" else "</b>")
        elif ct == "em_open":
            out.append("<i>")
            stack.append("</i>")
        elif ct == "em_close":
            out.append(stack.pop() if stack and stack[-1] == "</i>" else "</i>")
        elif ct == "s_open":
            out.append("<s>")
            stack.append("</s>")
        elif ct == "s_close":
            out.append(stack.pop() if stack and stack[-1] == "</s>" else "</s>")
        elif ct == "link_open":
            href = c.attrGet("href") or ""
            # Telegram requires URL-safe href; escape special chars
            href_esc = href.replace("&", "&amp;").replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
            out.append(f'<a href="{href_esc}">')
            stack.append("</a>")
        elif ct == "link_close":
            out.append(stack.pop() if stack and stack[-1] == "</a>" else "</a>")
        elif ct == "image":
            alt = c.attrGet("alt") or c.content or "image"
            out.append(f"[{esc(alt)}]")
        else:
            # Unknown inline token — render its content if any.
            if c.content:
                out.append(esc(c.content))
    # Close any leftover open tags.
    while stack:
        out.append(stack.pop())


def _md_to_tg_via_regex(text: str) -> str:
    """Legacy regex converter. Retained as a fallback only — markdown-it-py
    handles edge cases this misses (links with `]`, asterisks in URLs)."""
    s = (text.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))
    s = re.sub(r"```(?:[^\n]*\n)?(.*?)```", lambda m: f"<pre>{m.group(1)}</pre>", s, flags=re.DOTALL)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*\n][^*\n]*?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_\n][^_\n]*?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"(?<![_\w])_([^_\n]+?)_(?!_)", r"<i>\1</i>", s)
    s = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", s)
    s = re.sub(r"(?m)^[\-\*]\s+", "• ", s)
    return s


def _strip_markdown(text: str) -> str:
    """Plain-text fallback. Removes the markdown syntax characters so
    the message reads cleanly even without parse_mode."""
    s = text
    s = re.sub(r"```(?:[^\n]*\n)?(.*?)```", r"\1", s, flags=re.DOTALL)
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", s)
    s = re.sub(r"__([^_\n]+?)__", r"\1", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<![_\w])_([^_\n]+?)_(?!_)", r"\1", s)
    s = re.sub(r"~~([^~\n]+?)~~", r"\1", s)
    s = re.sub(r"(?m)^#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^[\-\*]\s+", "• ", s)
    return s


# Internal helper kept so any caller importing the old name still works.
def _tag_allowed(tag: str) -> bool:
    return tag.lower() in _TELEGRAM_ALLOWED_TAGS
