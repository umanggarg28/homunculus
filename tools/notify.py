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
import os
import re
import time
from collections import deque
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
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        return (
            "ERROR: Telegram is not configured (TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_ALLOWED_USER_ID missing). Cannot send notification."
        )

    if not text or not text.strip():
        return "ERROR: notify() requires non-empty text."

    if preview:
        body = _markdown_to_telegram_html(text)
        return (
            f"[PREVIEW · not sent] would push {len(text)} chars to Telegram:\n"
            f"---\n{body}\n---\n"
            f"Call notify(text) again without preview to actually send."
        )

    rate_err = _rate_limit_check()
    if rate_err:
        return rate_err

    body = _markdown_to_telegram_html(text)
    parse_failed = False
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        return f"ERROR: Telegram request failed: {e}"

    if response.status_code != 200:
        # Most common cause: HTML parse error from a stray `<` or unclosed
        # tag in the converted body. Fall back to plain text so the agent's
        # message still reaches the user.
        parse_failed = True
        plain = _strip_markdown(text)
        try:
            fallback = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": plain, "disable_web_page_preview": True},
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            return (
                f"ERROR: Telegram HTML send failed ({response.status_code}: "
                f"{response.text[:200]}) and plain-text fallback also failed: {e}"
            )
        if fallback.status_code != 200:
            return (
                f"ERROR: Telegram HTML send failed ({response.status_code}: "
                f"{response.text[:200]}) and plain-text fallback returned "
                f"{fallback.status_code}: {fallback.text[:200]}"
            )

    # Persist the notification so the Telegram bot's agent can pick it up
    # on the next user message. Without this bridge, a follow-up like
    # "explain it" arrives with zero context because the heartbeat
    # daemon and the bot run in separate processes with separate agent
    # histories. See Memory.drain_pending_notifications.
    _queue_for_telegram_history(text)
    suffix = " (plain-text fallback)" if parse_failed else ""
    return f"Notification delivered ({len(text)} chars){suffix}."


def _queue_for_telegram_history(text: str) -> None:
    """Append the sent text to memory/_notifications.jsonl so the
    Telegram bot can drain it into its agent's history before the
    next user message. Best-effort — failures logged but don't break notify."""
    try:
        memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
        memory_dir.mkdir(parents=True, exist_ok=True)
        queue_path = memory_dir / "_notifications.jsonl"
        entry = {"ts": time.time(), "text": text}
        with queue_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        # Don't silence — print so the user can see something went wrong
        # in the logs (notification arrived but bridge is broken).
        print(f"[notify] WARNING: could not queue for history: {e}", flush=True)


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
            out.append("<b>"); stack.append("</b>")
        elif ct == "strong_close":
            if stack and stack[-1] == "</b>": out.append(stack.pop())
            else: out.append("</b>")
        elif ct == "em_open":
            out.append("<i>"); stack.append("</i>")
        elif ct == "em_close":
            if stack and stack[-1] == "</i>": out.append(stack.pop())
            else: out.append("</i>")
        elif ct == "s_open":
            out.append("<s>"); stack.append("</s>")
        elif ct == "s_close":
            if stack and stack[-1] == "</s>": out.append(stack.pop())
            else: out.append("</s>")
        elif ct == "link_open":
            href = c.attrGet("href") or ""
            # Telegram requires URL-safe href; escape special chars
            href_esc = href.replace("&", "&amp;").replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
            out.append(f'<a href="{href_esc}">')
            stack.append("</a>")
        elif ct == "link_close":
            if stack and stack[-1] == "</a>": out.append(stack.pop())
            else: out.append("</a>")
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
