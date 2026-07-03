"""Skills route — per-tool usage stats for the Skills page."""

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from homunculus import events, tools
from homunculus.transports import web_api as wa

router = APIRouter()

# Keep at most this many recent tool_call timestamps per tool in the
# accumulator. The payload only ever shows the last 24h; the cap bounds
# accumulator memory across long uptimes.
_RECENT_CALLS_CAP = 50

#: Incremental per-tool counters over the event log. The log is append-only
#: between rotations, so after one full pass we remember (offset, counters)
#: and each request parses only the appended bytes — the same accounting
#: pattern as llm.py's budget scan. Counters are keyed by tool name
#: independent of tools.SCHEMAS, so a tool hot-reload never drops history.
#: Reset when the file shrinks (rotation).
_scan_state: dict[str, object] = {"offset": 0, "counters": {}}


def _counter(counters: dict, name: str) -> dict:
    entry = counters.get(name)
    if entry is None:
        entry = counters[name] = {
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_used": None,
            "last_status": None,
            "recent_calls": [],  # ISO ts of recent tool_call events
        }
    return entry


def _advance_scan() -> dict[str, dict]:
    """Bring the counter accumulator up to date with the event log."""
    offset = int(_scan_state["offset"])  # type: ignore[arg-type]
    counters: dict[str, dict] = _scan_state["counters"]  # type: ignore[assignment]

    path = wa.EVENTS_PATH
    try:
        size = path.stat().st_size
    except OSError:
        return counters
    if size < offset:
        # Rotation rewrote the file — everything must be recounted.
        offset = 0
        counters = {}

    records, next_offset = events.read_appended_records(path, offset)
    for rec in records:
        if rec.get("event") not in {"tool_call", "tool_result"}:
            continue
        name = rec.get("name")
        if not name:
            continue
        entry = _counter(counters, name)
        ts = rec.get("ts")
        if rec["event"] == "tool_call":
            entry["call_count"] += 1
            if ts:
                if entry["last_used"] is None or ts > entry["last_used"]:
                    entry["last_used"] = ts
                entry["recent_calls"].append(ts)
                if len(entry["recent_calls"]) > _RECENT_CALLS_CAP:
                    del entry["recent_calls"][:-_RECENT_CALLS_CAP]
        else:  # tool_result
            result = rec.get("result") or ""
            status = "failure" if str(result).lstrip().startswith("ERROR") else "success"
            entry[f"{status}_count"] += 1
            if ts and (entry["last_used"] is None or ts >= entry["last_used"]):
                entry["last_used"] = ts
                entry["last_status"] = status

    _scan_state["offset"] = next_offset
    _scan_state["counters"] = counters
    return counters


@router.get("/api/skills", dependencies=[Depends(wa.require_web_auth)])
def skills_list() -> JSONResponse:
    """Aggregate per-tool stats from tools.SCHEMAS + _events.jsonl.

    Used by the Skills page to answer "what does this agent reliably do?"
    — name + description + call count + success rate + last used.
    The event scan is incremental (byte offset) and the assembled payload
    is memoized briefly (wa.memo_ttl) to absorb page-load request bursts.
    """
    return JSONResponse(wa.memo_ttl("skills_list", 5.0, _aggregate_tool_stats))


def _aggregate_tool_stats() -> list[dict]:
    counters = _advance_scan()

    # The response is skeletoned from the LIVE tool catalogue and overlaid
    # with the accumulated counters — a tool added mid-session appears with
    # zero counts; a removed tool disappears but its history is retained in
    # the accumulator in case it returns.
    cutoff_iso = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    out: list[dict] = []
    for schema in tools.SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        c = counters.get(name) or _counter({}, name)
        out.append({
            "name": name,
            "description": fn.get("description", ""),
            "call_count": c["call_count"],
            "success_count": c["success_count"],
            "failure_count": c["failure_count"],
            "last_used": c["last_used"],
            "last_status": c["last_status"],
            # Trim to the last 24h at render time; the accumulator keeps a
            # capped raw list so old timestamps age out naturally.
            "recent_calls": [t for t in c["recent_calls"] if t >= cutoff_iso],
            "uses": None,
            "consecutive_failures": None,
        })

    # Overlay uses/consecutive_failures from the skill-stats sidecar (written
    # by rate_skill). These track agent-learned procedures — distinct from the
    # MCP tool call counts above. We match each skill file's frontmatter
    # `name:` to an entry, then read the counts from the sidecar, which is
    # keyed by the file stem (`skill_<slug>`).
    by_name = {e["name"]: e for e in out}
    mem_dir = wa.MEMORY_DIR if wa.MEMORY_DIR.exists() else None
    if mem_dir:
        from homunculus.stores import SkillStatsStore

        stats = SkillStatsStore(mem_dir).all()
        for skill_file in mem_dir.glob("skill_*.md"):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            name_m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
            if not name_m:
                continue
            entry = by_name.get(name_m.group(1).strip())
            if entry is None:
                continue
            s = stats.get(skill_file.stem)
            if s:
                entry["uses"] = s.get("uses")
                entry["consecutive_failures"] = s.get("consecutive_failures")

    return out
