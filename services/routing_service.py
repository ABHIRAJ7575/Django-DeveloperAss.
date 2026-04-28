"""
Routing service — wraps OpenRouteService (ORS) to geocode locations,
validate CONUS bounds, and retrieve driving-route geometry.
"""

import logging
import os
import time

import polyline as polyline_lib
import requests

from services.exceptions import LocationOutsideCONUSError, RoutingServiceUnavailableError

logger = logging.getLogger(__name__)

ORS_BASE_URL = "https://api.openrouteservice.org"

CONUS_BOUNDS = {
    "lat_min": 24.0,
    "lat_max": 50.0,
    "lon_min": -125.0,
    "lon_max": -66.0,
}

METRES_TO_MILES = 0.000621371
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3


def _api_key() -> str:
    key = os.environ.get("ORS_API_KEY", "")
    if not key:
        logger.error("ORS_API_KEY is not set in environment")
    return key


def _validate_conus(lat: float, lon: float, location_label: str = "Location") -> None:
    b = CONUS_BOUNDS
    if not (b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]):
        raise LocationOutsideCONUSError(
            f"{location_label} must be within the continental USA"
        )


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Make an HTTP request with up to MAX_RETRIES retries on transient errors."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            # Log non-2xx for debugging
            if not resp.ok:
                logger.error(
                    "ORS %s %s → HTTP %d | body: %s",
                    method.upper(), url, resp.status_code,
                    resp.text[:500],
                )
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout as exc:
            logger.warning("ORS request timed out (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            last_exc = exc
        except requests.exceptions.ConnectionError as exc:
            logger.warning("ORS connection error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            last_exc = exc
        except requests.exceptions.HTTPError as exc:
            # 4xx errors are not retryable
            raise RoutingServiceUnavailableError(f"Routing service unavailable: {exc}") from exc

        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)  # exponential backoff: 2s, 4s

    raise RoutingServiceUnavailableError(
        f"Routing service unavailable after {MAX_RETRIES} attempts: {last_exc}"
    )


def _geocode(location_text: str) -> tuple[float, float]:
    """
    Geocode location_text. Tries ORS first; falls back to Nominatim if ORS
    quota is exceeded or unavailable.
    Returns (lat, lon).
    """
    logger.debug("Geocoding: %r", location_text)

    # Try ORS first
    key = _api_key()
    if key:
        url = f"{ORS_BASE_URL}/geocode/search"
        try:
            resp = requests.get(
                url,
                params={"api_key": key, "text": location_text, "size": 1},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.ok:
                features = resp.json().get("features", [])
                if features:
                    lon, lat = features[0]["geometry"]["coordinates"][:2]
                    logger.debug("ORS geocoded %r → (%.4f, %.4f)", location_text, lat, lon)
                    return float(lat), float(lon)
            else:
                logger.warning("ORS geocode failed (HTTP %d) — falling back to Nominatim", resp.status_code)
        except Exception as exc:
            logger.warning("ORS geocode error: %s — falling back to Nominatim", exc)

    # Fallback: Nominatim (free, no quota)
    logger.debug("Using Nominatim for: %r", location_text)
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location_text, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "FuelRouteOptimizer/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            logger.debug("Nominatim geocoded %r → (%.4f, %.4f)", location_text, lat, lon)
            return lat, lon
    except Exception as exc:
        logger.error("Nominatim geocode error: %s", exc)

    raise RoutingServiceUnavailableError(
        f"Could not geocode location: {location_text!r}"
    )


def _get_directions(
    start_lon: float, start_lat: float,
    end_lon: float, end_lat: float,
) -> dict:
    """
    Call ORS /v2/directions/driving-car.
    ORS JWT keys go in the Authorization header (no "Bearer" prefix needed).
    """
    url = f"{ORS_BASE_URL}/v2/directions/driving-car"
    key = _api_key()

    # ORS accepts the JWT token directly as the Authorization value
    headers = {
        "Authorization": key,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }
    body = {
        "coordinates": [[start_lon, start_lat], [end_lon, end_lat]],
    }

    logger.debug(
        "Requesting directions: (%.4f, %.4f) → (%.4f, %.4f)",
        start_lat, start_lon, end_lat, end_lon,
    )

    try:
        resp = _request_with_retry("POST", url, json=body, headers=headers)
    except RoutingServiceUnavailableError:
        raise
    except Exception as exc:
        raise RoutingServiceUnavailableError(f"Routing service unavailable: {exc}") from exc

    return resp.json()


def _decode_polyline(encoded: str) -> list[list[float]]:
    return [[lat, lon] for lat, lon in polyline_lib.decode(encoded)]


def get_route(start_location: str, end_location: str) -> dict:
    """
    Retrieve a driving route between two US locations.

    Returns:
        {
            "polyline": [[lat, lon], ...],
            "distance_miles": float,
            "start_coords": [lat, lon],
            "end_coords": [lat, lon],
        }

    Raises:
        LocationOutsideCONUSError
        RoutingServiceUnavailableError
    """
    logger.info("get_route: %r → %r", start_location, end_location)

    # Geocode + validate start
    start_lat, start_lon = _geocode(start_location)
    _validate_conus(start_lat, start_lon, location_label="Start location")

    # Geocode + validate end
    end_lat, end_lon = _geocode(end_location)
    _validate_conus(end_lat, end_lon, location_label="End location")

    # Get directions
    ors_data = _get_directions(start_lon, start_lat, end_lon, end_lat)

    try:
        route = ors_data["routes"][0]
        distance_metres = route["summary"]["distance"]
        encoded_geometry = route["geometry"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected ORS response format: %s | response: %s", exc, ors_data)
        raise RoutingServiceUnavailableError(
            f"Unexpected ORS directions response format: {exc}"
        ) from exc

    decoded = _decode_polyline(encoded_geometry)
    distance_miles = distance_metres * METRES_TO_MILES

    logger.info(
        "Route found: %.1f miles, %d polyline points",
        distance_miles, len(decoded),
    )

    return {
        "polyline": decoded,
        "distance_miles": distance_miles,
        "start_coords": [start_lat, start_lon],
        "end_coords": [end_lat, end_lon],
    }
