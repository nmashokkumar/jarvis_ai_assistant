"""
Open-Meteo weather tool for JARVIS.
No API key required — uses the free Open-Meteo geocoding and forecast APIs.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → human-readable description
_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
}

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


async def _geocode(location: str) -> tuple[float, float, str]:
    """
    Resolve a location name to (latitude, longitude, resolved_name).
    Raises ValueError if the location is not found.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            _GEOCODE_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        r.raise_for_status()
        data = r.json()

    results = data.get("results")
    if not results:
        raise ValueError(f"Location '{location}' not found.")

    result = results[0]
    lat = result["latitude"]
    lon = result["longitude"]
    name = result.get("name", location)
    country = result.get("country", "")
    return lat, lon, f"{name}, {country}".strip(", ")


async def get_weather(location: str = "Chennai, India") -> str:
    """
    Fetch current weather for a given location using Open-Meteo.

    Args:
        location: free-text location string (city, country)

    Returns:
        Formatted weather summary string.
    """
    try:
        lat, lon, resolved = await _geocode(location)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("Geocoding error: %s", exc)
        return f"Couldn't resolve location '{location}': {exc}"

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": [
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "weather_code",
            "wind_speed_10m",
            "wind_direction_10m",
            "precipitation",
        ],
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_FORECAST_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Open-Meteo forecast error: %s", exc)
        return f"Weather fetch failed: {exc}"

    cur = data.get("current", {})
    temp = cur.get("temperature_2m", "N/A")
    feels = cur.get("apparent_temperature", "N/A")
    humidity = cur.get("relative_humidity_2m", "N/A")
    wind_speed = cur.get("wind_speed_10m", "N/A")
    wind_dir = cur.get("wind_direction_10m", "N/A")
    precip = cur.get("precipitation", 0)
    code = cur.get("weather_code", 0)
    condition = _WMO_CODES.get(int(code), f"Code {code}")

    return (
        f"Weather in {resolved}:\n"
        f"  Condition : {condition}\n"
        f"  Temperature: {temp}°C (feels like {feels}°C)\n"
        f"  Humidity  : {humidity}%\n"
        f"  Wind      : {wind_speed} km/h from {wind_dir}°\n"
        f"  Precipitation: {precip} mm"
    )
