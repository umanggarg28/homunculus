"""Tests for user_location config + the weather tool's location handling.

Network is mocked — these assert the config round-trip, validation, the
unset-vs-set behavior, and that weather/geocode parse Open-Meteo responses
correctly without hitting the API.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import user_location
from tests.conftest import load_real_tool_submodule

weather = load_real_tool_submodule("weather")


@pytest.fixture(autouse=True)
def _isolated_location_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_LOCATION_FILE", str(tmp_path / "loc.txt"))
    # Reset the module cache between cases.
    user_location._cached = None
    user_location._cached_mtime = None
    user_location._cached_path = None
    yield


def test_unset_returns_none():
    assert user_location.get_user_location() is None


def test_set_and_get_round_trip():
    stored = user_location.set_user_location(12.9716, 77.5946, "Bengaluru")
    assert stored == {"lat": 12.9716, "lon": 77.5946, "label": "Bengaluru"}
    assert user_location.get_user_location() == stored


def test_out_of_range_rejected():
    assert user_location.set_user_location(200.0, 0.0, "nowhere") is None
    assert user_location.get_user_location() is None


def test_non_numeric_rejected():
    assert user_location.set_user_location("abc", "def") is None  # type: ignore[arg-type]


def test_label_optional_and_trimmed():
    stored = user_location.set_user_location(1.0, 2.0)
    assert stored["label"] == ""
    stored = user_location.set_user_location(1.0, 2.0, "  Paris\n ")
    assert stored["label"] == "Paris"


def test_cache_invalidated_on_rewrite():
    user_location.set_user_location(1.0, 2.0, "A")
    assert user_location.get_user_location()["label"] == "A"
    user_location.set_user_location(3.0, 4.0, "B")
    assert user_location.get_user_location()["label"] == "B"


def test_weather_unset_does_not_guess():
    out = weather.get_weather()
    assert "WEATHER UNAVAILABLE" in out
    assert "no home location" in out


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_weather_parses_forecast():
    user_location.set_user_location(12.97, 77.59, "Bengaluru")
    payload = {
        "daily": {
            "temperature_2m_max": [31.4],
            "temperature_2m_min": [21.2],
            "weather_code": [51],
        }
    }
    with patch.object(weather.httpx, "get", return_value=_Resp(payload)):
        out = weather.get_weather()
    assert "Bengaluru" in out
    assert "light drizzle" in out
    assert "high 31" in out and "low 21" in out


def test_geocode_parses_result():
    payload = {"results": [{
        "latitude": 48.8566, "longitude": 2.3522,
        "name": "Paris", "admin1": "Île-de-France", "country": "France",
    }]}
    with patch.object(weather.httpx, "get", return_value=_Resp(payload)):
        geo = weather.geocode_city("Paris")
    assert geo["lat"] == 48.8566
    assert geo["label"] == "Paris, Île-de-France, France"


def test_geocode_empty_returns_none():
    with patch.object(weather.httpx, "get", return_value=_Resp({"results": []})):
        assert weather.geocode_city("zzzznowhere") is None
    assert weather.geocode_city("") is None
