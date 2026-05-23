"""Web tools: web_search, web_fetch. Cached on disk."""

from __future__ import annotations

import os

import httpx

from ._helpers import (
    READ_FILE_MAX_CHARS,
    WEB_FETCH_CACHE_SECONDS,
    WEB_SEARCH_CACHE_SECONDS,
    cache_get,
    cache_set,
)


def web_search(query: str) -> str:
    cache_id = query.strip().lower()
    cached = cache_get("web_search", cache_id, WEB_SEARCH_CACHE_SECONDS)
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
    if data.get("answer"):
        lines.append(f"Answer summary: {data['answer']}\n")
    lines.append(f"Top results for '{query}':")
    for i, result in enumerate(data.get("results", []), 1):
        lines.append(f"\n[{i}] {result.get('title', '(no title)')}")
        lines.append(f"    URL: {result.get('url', '')}")
        snippet = (result.get("content") or "").strip()
        if snippet:
            if len(snippet) > 500:
                snippet = snippet[:500] + "..."
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def web_fetch(url: str) -> str:
    cache_id = url.strip()
    cached = cache_get("web_fetch", cache_id, WEB_FETCH_CACHE_SECONDS)
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

    if len(text) > READ_FILE_MAX_CHARS:
        text = (
            text[:READ_FILE_MAX_CHARS]
            + f"\n\n[...{len(text) - READ_FILE_MAX_CHARS} chars truncated]"
        )
    cache_set("web_fetch", cache_id, text)
    return text


