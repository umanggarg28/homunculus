"""User-preference routes — timezone, home location, and plan/build mode.

Identity-type settings the user configures explicitly (never guessed by the
model): the IANA timezone, the home location for weather, and the agent's
plan/build execution mode.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from homunculus import tools
from homunculus.transports import web_api as wa

router = APIRouter()


@router.post("/api/user-tz")
async def user_tz_set(request: Request) -> JSONResponse:
    """Persist the browser-detected timezone so heartbeat and agent tools
    can use it. Called by the web UI on first load.

    Body: {"tz": "Asia/Kolkata"} — an IANA timezone name.
    Invalid names are silently ignored (better than 4xx-ing the user's
    perfectly normal session for a TZ we can't parse).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "reason": "invalid json"}, status_code=400)
    tz = (body or {}).get("tz") if isinstance(body, dict) else None
    if not isinstance(tz, str) or not tz:
        return JSONResponse({"ok": False, "reason": "missing tz"}, status_code=400)
    try:
        from homunculus.user_tz import get_user_tz_name, set_user_tz_name
        set_user_tz_name(tz)
        return JSONResponse({"ok": True, "stored": get_user_tz_name()})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@router.get("/api/user-tz")
def user_tz_get() -> JSONResponse:
    """Return the currently stored user TZ (for debugging / UI display)."""
    try:
        from homunculus.user_tz import get_user_tz_name
        return JSONResponse({"tz": get_user_tz_name()})
    except Exception as e:
        return JSONResponse({"tz": "UTC", "error": str(e)})


@router.post("/api/user-location")
async def user_location_set(request: Request) -> JSONResponse:
    """Persist the user's home location so the weather tool and heartbeat can
    use it. Called by the web UI once — sibling of /api/user-tz.

    Body, either:
      {"lat": 12.97, "lon": 77.59, "label": "Bengaluru"}   (browser geolocation)
      {"city": "Bengaluru"}                                 (typed fallback → geocoded)

    Location is configured here, never guessed by the model.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "reason": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "reason": "missing body"}, status_code=400)
    from homunculus.user_location import set_user_location

    lat, lon, label = body.get("lat"), body.get("lon"), body.get("label", "")
    if lat is None or lon is None:
        # No coordinates → treat as a typed city and geocode it (Open-Meteo).
        city = body.get("city")
        if not isinstance(city, str) or not city.strip():
            return JSONResponse({"ok": False, "reason": "need lat+lon or city"}, status_code=400)
        from homunculus.tools.weather import geocode_city
        geo = geocode_city(city)
        if not geo:
            return JSONResponse({"ok": False, "reason": f"could not geocode {city!r}"}, status_code=404)
        lat, lon, label = geo["lat"], geo["lon"], geo["label"]
    stored = set_user_location(lat, lon, str(label or ""))
    if not stored:
        return JSONResponse({"ok": False, "reason": "invalid coordinates"}, status_code=400)
    return JSONResponse({"ok": True, "stored": stored})


@router.get("/api/user-location")
def user_location_get() -> JSONResponse:
    """Return the stored home location, or {"location": null} if unset."""
    try:
        from homunculus.user_location import get_user_location
        return JSONResponse({"location": get_user_location()})
    except Exception as e:
        return JSONResponse({"location": None, "error": str(e)})


@router.get("/api/mode", dependencies=[Depends(wa.require_web_auth)])
def mode_get() -> JSONResponse:
    return JSONResponse({"mode": tools.get_mode()})


@router.post("/api/mode", dependencies=[Depends(wa.require_web_auth)])
async def mode_set(request: Request) -> JSONResponse:
    body = await request.json()
    mode = (body or {}).get("mode")
    if mode not in {"plan", "build"}:
        raise HTTPException(400, "mode must be 'plan' or 'build'")
    tools.set_mode(mode)
    return JSONResponse({"mode": tools.get_mode()})
