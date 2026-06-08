"""Web tools: web_search, web_fetch. Cached on disk."""

from __future__ import annotations

import os

import httpx

from config import get_config

from ._helpers import (
    cache_get,
    cache_set,
)


def web_search(query: str) -> str:
    cache_id = query.strip().lower()
    cached = cache_get("web_search", cache_id, get_config().cache.web_search_seconds)
    if cached is not None:
        return f"[cache hit]\n{cached}"

    provider = os.environ.get("WEB_SEARCH_PROVIDER", "tavily").lower()
    if provider == "tavily":
        result = _search_tavily(query)
        if not result.startswith("ERROR:"):
            cache_set("web_search", cache_id, result)
        return result
    return f"ERROR: web search provider '{provider}' not implemented"


def _search_tavily(query: str) -> str:
    """Tavily backend. Free tier at https://tavily.com (1000/month)."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return (
            "ERROR: TAVILY_API_KEY not set in .env. Get a free key at "
            "https://tavily.com (1000 searches/month free)."
        )
    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": 5,
                "include_answer": True,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        return f"ERROR: Tavily request failed: {e}"
    if response.status_code != 200:
        return f"ERROR: Tavily API {response.status_code}: {response.text}"
    data = response.json()

    lines: list[str] = []
    # Direct answer comes first — use it if present, it's the most reliable signal.
    if data.get("answer"):
        lines.append(f"DIRECT ANSWER: {data['answer']}")
        lines.append("")
    lines.append(f"Supporting results for '{query}':")
    for i, result in enumerate(data.get("results", []), 1):
        lines.append(f"\n[{i}] {result.get('title', '(no title)')}")
        lines.append(f"    URL: {result.get('url', '')}")
        snippet = (result.get("content") or "").strip()
        if snippet:
            if len(snippet) > 400:
                snippet = snippet[:400] + "..."
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def web_fetch(url: str) -> str:
    cache_id = url.strip()
    cached = cache_get("web_fetch", cache_id, get_config().cache.web_fetch_seconds)
    if cached is not None:
        return f"[cache hit]\n{cached}"

    try:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Homunculus AI assistant)"},
        )
    except httpx.HTTPError as e:
        return f"ERROR: fetch failed: {e}"
    if response.status_code in {401, 403, 429}:
        result = (
            f"BLOCKED: HTTP {response.status_code} fetching {url}. "
            "The site likely blocks automated fetches. Do not retry this "
            "same URL; use web_search snippets, your own knowledge, or "
            "fetch another accessible source."
        )
        cache_set("web_fetch", cache_id, result)
        return result
    if response.status_code != 200:
        return f"ERROR: HTTP {response.status_code} fetching {url}"

    content_type = response.headers.get("content-type", "").lower()
    text = response.text

    if "html" in content_type:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = "\n".join(line for line in text.splitlines() if line.strip())

    max_chars = get_config().loop.read_file_max_chars
    if len(text) > max_chars:
        text = (
            text[:max_chars]
            + f"\n\n[...{len(text) - max_chars} chars truncated]"
        )
    cache_set("web_fetch", cache_id, text)
    return text


def web_post(
    url: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    raw_body: str | None = None,
) -> str:
    """POST to a URL and return the response text.

    Sibling of web_fetch for verbs that need a request body. Primary use
    case: skill-refinement agents verifying API endpoints (GraphQL
    queries, REST POSTs to documented APIs) before saving a new skill
    body that depends on them. The python tool's sandbox blocks
    network — this is the surfaced primitive.

    Not cached: POST results depend on the body and are commonly
    mutating. Callers that need idempotent POSTs (e.g. GraphQL reads)
    can layer their own caching above the tool.

    Body handling:
      - `json_body` → application/json (sent via httpx's json= for
        proper encoding)
      - `raw_body` → sent verbatim (caller sets Content-Type via
        `headers`); takes precedence over `json_body` if both supplied
      - Neither → empty body POST

    Response handling mirrors web_fetch: HTML stripped to text, length
    capped at config.loop.read_file_max_chars.
    """
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Homunculus AI assistant)",
        **(headers or {}),
    }
    try:
        if raw_body is not None:
            response = httpx.post(
                url,
                content=raw_body.encode("utf-8"),
                headers=request_headers,
                timeout=30.0,
                follow_redirects=True,
            )
        else:
            response = httpx.post(
                url,
                json=(json_body if json_body is not None else {}),
                headers=request_headers,
                timeout=30.0,
                follow_redirects=True,
            )
    except httpx.HTTPError as e:
        return f"ERROR: POST failed: {e}"

    if response.status_code in {401, 403, 429}:
        return (
            f"BLOCKED: HTTP {response.status_code} POSTing to {url}. "
            "The endpoint likely blocks automated calls or your auth "
            "header is missing/wrong. Inspect the response shape with "
            "a different endpoint or fix headers before retrying."
        )
    if response.status_code >= 400:
        # 4xx/5xx still returned to the caller — GraphQL endpoints often
        # respond 200 with errors in the body, but some validation errors
        # come back as 400 with a useful body. Show it; let the caller
        # decide if it's actionable.
        return (
            f"HTTP {response.status_code} from {url}\n"
            f"{response.text[:2000]}"
        )

    content_type = response.headers.get("content-type", "").lower()
    text = response.text
    if "html" in content_type:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = "\n".join(line for line in text.splitlines() if line.strip())

    max_chars = get_config().loop.read_file_max_chars
    if len(text) > max_chars:
        text = (
            text[:max_chars]
            + f"\n\n[...{len(text) - max_chars} chars truncated]"
        )
    return text


