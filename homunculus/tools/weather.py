"""Weather tool — today's forecast for the user's configured home location.

Open-Meteo (https://open-meteo.com): free, no API key, fits the $5/mo budget.
Two endpoints used here:
  - forecast: daily max/min temp + a WMO weather code for a lat/lon.
  - geocoding: city name → lat/lon (used by the web API's typed-city fallback,
    re-exported here so the location plumbing lives in one place).

Location is read from user_location config — NEVER passed by the model. The
weak model supplying coordinates is the fabricated-identifier failure mode
(see the morning-brief incident); location is configured once via the browser.
"""

from __future__ import annotations

import httpx

from homunculus.user_location import get_user_location
from homunculus.sentinels import WEATHER_UNAVAILABLE

# WMO weather interpretation codes → short human text.
# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
_WMO: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def geocode_city(name: str) -> dict[str, float | str] | None:
    """Resolve a city name to {lat, lon, label} via Open-Meteo geocoding, or
    None if not found. Used by the web API when the user types a city instead
    of granting browser geolocation."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        r = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "en", "format": "json"},
            timeout=15.0,
        )
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not results:
        return None
    top = results[0]
    try:
        lat = float(top["latitude"])
        lon = float(top["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    label_bits = [top.get("name"), top.get("admin1"), top.get("country")]
    label = ", ".join(b for b in label_bits if b)
    return {"lat": lat, "lon": lon, "label": label}


def get_weather() -> str:
    """Today's weather for the user's configured home location.

    Returns a one-line summary (condition, high/low °C). If no location is
    configured, returns a clear instruction to set one — the tool never
    guesses a location, so a brief that needs weather will correctly report
    "not set" instead of fabricating a forecast.
    """
    loc = get_user_location()
    if not loc:
        return (
            f"{WEATHER_UNAVAILABLE}: no home location is configured. Set it on "
            "the web app (it's captured once from your browser) — until then, "
            "omit weather from the brief rather than guessing."
        )
    lat, lon, label = loc["lat"], loc["lon"], loc["label"]
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=20.0,
        )
        r.raise_for_status()
        daily = (r.json() or {}).get("daily") or {}
        hi = daily["temperature_2m_max"][0]
        lo = daily["temperature_2m_min"][0]
        code = int(daily["weather_code"][0])
    except httpx.HTTPError as e:
        return f"{WEATHER_UNAVAILABLE}: forecast request failed ({e}). Omit weather rather than guessing."
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return f"{WEATHER_UNAVAILABLE}: unexpected forecast response ({e}). Omit weather rather than guessing."
    condition = _WMO.get(code, "unknown conditions")
    where = f" in {label}" if label else ""
    return f"Weather{where}: {condition}, high {round(hi)}°C, low {round(lo)}°C."
