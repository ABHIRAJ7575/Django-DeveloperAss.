import os
import json
import logging
import time
import requests
import pandas as pd

from services.exceptions import DatasetLoadError

logger = logging.getLogger(__name__)

# BASE_DIR is two levels up from services/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSV_PATH = os.path.join(BASE_DIR, "data", "fuel-prices-for-be-assessment.csv")
CACHE_PATH = os.path.join(BASE_DIR, "data", "geocoded_stops.json")

ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace and lowercase column names for case-insensitive matching."""
    df.columns = [c.strip() for c in df.columns]
    col_map = {}
    for col in df.columns:
        lower = col.lower()
        if lower == "truckstop name":
            col_map[col] = "name"
        elif lower == "address":
            col_map[col] = "address"
        elif lower == "city":
            col_map[col] = "city"
        elif lower == "state":
            col_map[col] = "state"
        elif lower == "retail price":
            col_map[col] = "retail_price"
    return df.rename(columns=col_map)


def _load_cache() -> dict:
    """Load geocoded_stops.json if it exists, else return empty cache."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 1:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "stops": []}


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _geocode_via_ors(address: str, city: str, state: str) -> tuple[float, float] | None:
    """Geocode via OpenRouteService. Returns (lat, lon) or None."""
    api_key = os.environ.get("ORS_API_KEY", "")
    if not api_key:
        return None
    query = f"{address}, {city}, {state}, USA"
    try:
        resp = requests.get(
            ORS_GEOCODE_URL,
            params={"api_key": api_key, "text": query, "size": 1,
                    "boundary.country": "US"},
            timeout=10,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            return None
        coords = features[0]["geometry"]["coordinates"]  # [lon, lat]
        return coords[1], coords[0]  # (lat, lon)
    except Exception:
        return None


def _geocode_via_nominatim(address: str, city: str, state: str) -> tuple[float, float] | None:
    """Geocode via Nominatim (free, no key needed). Returns (lat, lon) or None."""
    query = f"{address}, {city}, {state}, USA"
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "FuelRouteOptimizer/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            # Try city+state only as fallback
            resp2 = requests.get(
                NOMINATIM_URL,
                params={"q": f"{city}, {state}, USA", "format": "json",
                        "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": "FuelRouteOptimizer/1.0"},
                timeout=10,
            )
            resp2.raise_for_status()
            results = resp2.json()
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        return None


def _geocode_stop(address: str, city: str, state: str) -> tuple[float, float] | None:
    """
    Geocode a stop. Uses Nominatim (ORS geocode quota is limited on free tier).
    Returns (latitude, longitude) or None on failure.
    """
    # Try Nominatim with full address first, then city+state fallback
    for query in [f"{address}, {city}, {state}, USA", f"{city}, {state}, USA"]:
        try:
            time.sleep(1.1)  # Nominatim requires ≤1 req/sec
            resp = requests.get(
                NOMINATIM_URL,
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": "FuelRouteOptimizer/1.0"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception:
            pass
    return None


def load_fuel_dataset() -> list[dict]:
    """
    Load fuel stops from the geocode cache only (fast, no network calls).

    Stops not yet in the cache are skipped at startup — run
    `python scripts/geocode_stops.py` to populate the cache in the background.

    Returns list[dict] with keys: name, address, city, state,
    latitude, longitude, retail_price.

    Raises:
        DatasetLoadError: if the CSV is missing or malformed.
    """
    # 1. Read CSV
    if not os.path.exists(CSV_PATH):
        raise DatasetLoadError(f"Fuel prices CSV not found: {CSV_PATH}")

    try:
        df = pd.read_csv(CSV_PATH, dtype=str)
    except Exception as exc:
        raise DatasetLoadError(f"Failed to read fuel prices CSV: {exc}") from exc

    df = _normalize_columns(df)

    required = {"name", "address", "city", "state", "retail_price"}
    missing = required - set(df.columns)
    if missing:
        raise DatasetLoadError(f"CSV is missing required columns: {missing}")

    for col in ("name", "address", "city", "state"):
        df[col] = df[col].str.strip()

    df["retail_price"] = pd.to_numeric(df["retail_price"], errors="coerce")
    df = df.dropna(subset=["retail_price"])

    if df.empty:
        raise DatasetLoadError("Fuel prices CSV contains no valid rows.")

    # 2. Deduplicate: keep lowest price per (name, address, city, state)
    df = df.loc[df.groupby(["name", "address", "city", "state"])["retail_price"].idxmin()]
    df = df.reset_index(drop=True)

    logger.info("CSV: %d unique stops after deduplication", len(df))

    # 3. Load geocode cache
    cache = _load_cache()
    cache_lookup: dict[tuple, dict] = {
        (s["name"], s["address"], s["city"], s["state"]): s
        for s in cache["stops"]
    }
    logger.info("Geocode cache: %d stops available", len(cache_lookup))

    # 4. Build result from cache only — no live geocoding at startup
    result: list[dict] = []
    uncached = 0

    for _, row in df.iterrows():
        key = (row["name"], row["address"], row["city"], row["state"])
        cached = cache_lookup.get(key)

        if cached and cached.get("latitude") is not None and cached.get("longitude") is not None:
            result.append({
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "latitude": cached["latitude"],
                "longitude": cached["longitude"],
                "retail_price": float(row["retail_price"]),
            })
        else:
            uncached += 1

    logger.info(
        "Fuel dataset ready: %d stops loaded from cache, %d not yet geocoded "
        "(run `python scripts/geocode_stops.py` to geocode remaining stops)",
        len(result), uncached,
    )

    return result
