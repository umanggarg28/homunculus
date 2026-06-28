"""Skills route — per-tool usage stats for the Skills page."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from homunculus import tools
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/skills", dependencies=[Depends(wa.require_web_auth)])
def skills_list() -> JSONResponse:
    """Aggregate per-tool stats from tools.SCHEMAS + _events.jsonl.

    Used by the Skills page to answer "what does this agent reliably do?"
    — name + description + call count + success rate + last used.
    """
    by_name: dict[str, dict] = {}
    for schema in tools.SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        by_name[name] = {
            "name": name,
            "description": fn.get("description", ""),
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_used": None,
            "last_status": None,
            "recent_calls": [],  # ISO ts of tool_call events in the last 24h
        }

    if wa.EVENTS_PATH.exists():
        with wa.EVENTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") not in {"tool_call", "tool_result"}:
                    continue
                name = rec.get("name")
                entry = by_name.get(name)
                if entry is None:
                    continue
                ts = rec.get("ts")
                if rec["event"] == "tool_call":
                    entry["call_count"] += 1
                    if ts:
                        if entry["last_used"] is None or ts > entry["last_used"]:
                            entry["last_used"] = ts
                        entry["recent_calls"].append(ts)
                else:  # tool_result
                    result = rec.get("result") or ""
                    is_failure = result.lstrip().startswith("ERROR")
                    if is_failure:
                        entry["failure_count"] += 1
                        status = "failure"
                    else:
                        entry["success_count"] += 1
                        status = "success"
                    if ts and (entry["last_used"] is None or ts >= entry["last_used"]):
                        entry["last_used"] = ts
                        entry["last_status"] = status

    # Trim recent_calls to last 24h to keep the payload small.
    cutoff_dt = datetime.now()
    cutoff_iso = (cutoff_dt - timedelta(days=1)).isoformat(timespec="seconds")
    for entry in by_name.values():
        entry["recent_calls"] = [t for t in entry["recent_calls"] if t >= cutoff_iso]
        entry["uses"] = None
        entry["consecutive_failures"] = None

    # Overlay uses/consecutive_failures from the skill-stats sidecar (written
    # by rate_skill). These track agent-learned procedures — distinct from the
    # MCP tool call counts above. We match each skill file's frontmatter
    # `name:` to a by_name entry, then read the counts from the sidecar, which
    # is keyed by the file stem (`skill_<slug>`).
    mem_dir = wa.MEMORY_DIR if wa.MEMORY_DIR.exists() else None
    if mem_dir:
        import re as _re

        from homunculus.stores import SkillStatsStore

        stats = SkillStatsStore(mem_dir).all()
        for skill_file in mem_dir.glob("skill_*.md"):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            name_m = _re.search(r"^name:\s*(.+)$", text, _re.MULTILINE)
            if not name_m:
                continue
            entry = by_name.get(name_m.group(1).strip())
            if entry is None:
                continue
            s = stats.get(skill_file.stem)
            if s:
                entry["uses"] = s.get("uses")
                entry["consecutive_failures"] = s.get("consecutive_failures")

    return JSONResponse(list(by_name.values()))
