"""LeetCode tool — the next undelivered Top Interview 150 problem, deterministically.

The daily-leetcode skill used to ask the weak model to "select the first problem
under the Problem order not yet delivered." There was no such ordered list in the
prompt or workspace, so on a bad day the model invented a search, mangled it into
an invalid regex, and burned its whole iteration budget looking for a file that
doesn't exist. The mechanical work — *what is the next problem* — is exactly the
kind of thing a weak model can't do reliably but code can.

So this tool owns the selection end to end:
  1. Fetch the canonical ordered "Top Interview 150" list from LeetCode's own
     GraphQL study-plan endpoint. The list is *data from the source*, never
     hardcoded — it's cached to disk so a transient LeetCode outage doesn't break
     the daily task, but the cache is always a real fetched copy.
  2. Read the task's delivery ledger (the harness-owned ``delivered`` keys) to
     know what has already been sent.
  3. Return the single next undelivered problem with its real canonical URL.

The model then only has to *explain and solve* the problem it's handed — work it
can actually do — instead of *finding* it. Mirrors news_headlines: sources are
data, fetch is code.
"""

from __future__ import annotations
from homunculus.sentinels import LEETCODE_NEXT_UNAVAILABLE

import json
import os
import time
from pathlib import Path

import httpx

_GRAPHQL_URL = "https://leetcode.com/graphql"
_PLAN_SLUG = "top-interview-150"
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Homunculus AI assistant)",
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com/studyplan/top-interview-150/",
}

# The study plan changes rarely; refetch at most once a day. The on-disk copy is
# a cached *fetch result* (sourced data), not a baked-in list — it exists so a
# LeetCode blip at 9am doesn't fail the daily delivery.
_CACHE_TTL_S = 24 * 3600
_CACHE_PATH = Path(
    os.environ.get(
        "HOMUNCULUS_LEETCODE_CACHE",
        os.path.join(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"), "_leetcode_top150.json"),
    )
)

# In-process cache so repeated calls within one long-running service process
# don't refetch. (slug list, fetched_at_epoch)
_mem_cache: tuple[list[dict], float] | None = None

_STUDY_PLAN_QUERY = (
    "query studyPlanDetail($slug: String!) {"
    "  studyPlanV2Detail(planSlug: $slug) {"
    "    planSubGroups { questions { titleSlug questionFrontendId title difficulty } }"
    "  }"
    "}"
)


def _fetch_ordered_problems() -> list[dict]:
    """Fetch the ordered Top Interview 150 list from LeetCode's GraphQL API.

    Returns a list of ``{"id", "slug", "title", "difficulty"}`` in plan order,
    or ``[]`` on any error (caller falls back to the on-disk cache).
    """
    try:
        resp = httpx.post(
            _GRAPHQL_URL,
            headers=_HEADERS,
            json={"query": _STUDY_PLAN_QUERY, "variables": {"slug": _PLAN_SLUG}},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        detail = (resp.json().get("data") or {}).get("studyPlanV2Detail") or {}
    except (httpx.HTTPError, ValueError):
        return []
    out: list[dict] = []
    for sub in detail.get("planSubGroups") or []:
        for q in sub.get("questions") or []:
            slug = q.get("titleSlug")
            if slug:
                out.append({
                    "id": q.get("questionFrontendId", ""),
                    "slug": slug,
                    "title": q.get("title", slug),
                    "difficulty": (q.get("difficulty") or "").title(),
                })
    return out


def _ordered_problems() -> list[dict]:
    """The Top-150 list, preferring a fresh fetch, then memory, then disk cache.

    A successful fetch refreshes both caches. If the API is unreachable we fall
    back to the last good on-disk copy (any age) so the daily task survives an
    outage rather than failing on the one day LeetCode is down.
    """
    global _mem_cache
    now = time.time()
    if _mem_cache is not None and now - _mem_cache[1] < _CACHE_TTL_S:
        return _mem_cache[0]

    fetched = _fetch_ordered_problems()
    if fetched:
        _mem_cache = (fetched, now)
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(
                json.dumps({"fetched_at": now, "problems": fetched}), encoding="utf-8"
            )
        except OSError:
            pass
        return fetched

    # Fetch failed — fall back to whatever we last persisted.
    if _mem_cache is not None:
        return _mem_cache[0]
    try:
        cached = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        problems = cached.get("problems") or []
        if problems:
            _mem_cache = (problems, cached.get("fetched_at", 0.0))
            return problems
    except (OSError, ValueError):
        pass
    return []


def _delivered_slugs(task_id: str = "") -> set[str]:
    """The slugs already delivered, from the task's harness-owned ledger.

    Reading the ledger directly (rather than trusting a model-supplied list)
    keeps the dedup deterministic. ``task_id`` is honored when given; otherwise
    we auto-detect the leetcode task by its skill so the tool works even if the
    model omits the argument.
    """
    # Lazy import: keeps module load free of the scheduling/TaskStore chain
    # (which also lets tests load this tool without the real tools package).
    from .scheduling import _task_store

    store = _task_store()
    task = store.get(task_id) if task_id else None
    if task is None:
        for t in store.all():
            tid = str(t.get("id", "")).lower()
            if t.get("skill") == "skill_deliver_daily_leetcode" or "leetcode" in tid:
                task = t
                break
    if not task:
        return set()
    return {d.get("key") for d in (task.get("delivered") or []) if d.get("key")}


def leetcode_next_problem(task_id: str = "") -> str:
    """Return the next undelivered LeetCode Top Interview 150 problem.

    Deterministically picks the first problem in the official plan order that is
    not yet in the task's delivery ledger, and returns it with its real
    canonical URL. Use the returned URL verbatim in the message. ``task_id`` is
    optional — when omitted the daily-leetcode task is detected automatically.

    Returns ``LEETCODE_NEXT_UNAVAILABLE: <reason>`` if the list can't be loaded,
    or ``LEETCODE_ALL_DELIVERED`` once every problem has been sent.
    """
    problems = _ordered_problems()
    if not problems:
        return (
            f"{LEETCODE_NEXT_UNAVAILABLE}: could not load the Top Interview 150 list "
            "from LeetCode (API unreachable and no cached copy). Skip the LeetCode "
            "delivery this run and record a failure."
        )
    delivered = _delivered_slugs(task_id)
    for q in problems:
        if q["slug"] not in delivered:
            url = f"https://leetcode.com/problems/{q['slug']}/"
            remaining = sum(1 for p in problems if p["slug"] not in delivered)
            diff = f" ({q['difficulty']})" if q["difficulty"] else ""
            return (
                f"NEXT PROBLEM: #{q['id']} {q['title']}{diff}\n"
                f"slug: {q['slug']}\n"
                f"url: {url}\n"
                f"({remaining} of {len(problems)} problems still undelivered)"
            )
    return (
        "LEETCODE_ALL_DELIVERED: every Top Interview 150 problem has already been "
        "delivered. Tell the user they have completed the list."
    )
