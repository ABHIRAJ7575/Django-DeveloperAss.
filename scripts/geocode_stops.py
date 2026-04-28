"""
One-time script to geocode all fuel stops and populate data/geocoded_stops.json.

Run from the project root:
    python scripts/geocode_stops.py

This uses ORS geocoding (fast, uses your API key) with Nominatim as fallback.
Progress is saved after every 50 stops so you can safely interrupt and resume.
"""
import os
import sys
import json
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "fuel-prices-for-be-assessment.csv")
CACHE_PATH = os.path.join(BASE_DIR, "data", "geocoded_stops.json")

ORS_KEY = os.environ.get("ORS_API_KEY", "")
ORS_URL = "https://api.openrouteservice.org/geocode/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                d = json.load(f)
            if d.get("version") == 1:
                return d
        except Exception:
            pass
    return {"version": 1, "stops": []}


def save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def geocode_ors(address, city, state):
    if not ORS_KEY:
        return None
    try:
        r = requests.get(ORS_URL, params={
            "api_key": ORS_KEY,
            "text": f"{address}, {city}, {state}, USA",
            "size": 1,
            "boundary.country": "US",
        }, timeout=10)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            return float(lat), float(lon)
    except Exception:
        pass
    return None


def geocode_nominatim(address, city, state):
    for query in [f"{address}, {city}, {state}, USA", f"{city}, {state}, USA"]:
        try:
            time.sleep(1.1)  # Nominatim rate limit
            r = requests.get(NOMINATIM_URL, params={
                "q": query, "format": "json", "limit": 1, "countrycodes": "us"
            }, headers={"User-Agent": "FuelRouteOptimizer/1.0"}, timeout=10)
            r.raise_for_status()
            results = r.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception:
            pass
    return None


def main():
    print(f"Reading CSV: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # Normalize column names
    rename = {}
    for col in df.columns:
        lc = col.lower()
        if lc == "truckstop name":
            rename[col] = "name"
        elif lc == "address":
            rename[col] = "address"
        elif lc == "city":
            rename[col] = "city"
        elif lc == "state":
            rename[col] = "state"
        elif lc == "retail price":
            rename[col] = "retail_price"
    df = df.rename(columns=rename)

    for col in ("name", "address", "city", "state"):
        df[col] = df[col].str.strip()
    df["retail_price"] = pd.to_numeric(df["retail_price"], errors="coerce")
    df = df.dropna(subset=["retail_price"])
    df = df.loc[df.groupby(["name", "address", "city", "state"])["retail_price"].idxmin()]
    df = df.reset_index(drop=True)

    print(f"Total unique stops after dedup: {len(df)}")

    cache = load_cache()
    cache_lookup = {
        (s["name"], s["address"], s["city"], s["state"]): s
        for s in cache["stops"]
    }

    already_cached = sum(
        1 for _, row in df.iterrows()
        if (row["name"], row["address"], row["city"], row["state"]) in cache_lookup
    )
    to_geocode = len(df) - already_cached
    print(f"Already cached: {already_cached} | Need to geocode: {to_geocode}")

    if to_geocode == 0:
        print("All stops already geocoded!")
        return

    geocoded = 0
    skipped = 0
    save_interval = 50

    for i, (_, row) in enumerate(df.iterrows()):
        key = (row["name"], row["address"], row["city"], row["state"])
        if key in cache_lookup:
            continue

        coords = geocode_nominatim(row["address"], row["city"], row["state"])
        method = "Nominatim"

        if coords:
            lat, lon = coords
            entry = {
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "latitude": lat,
                "longitude": lon,
                "retail_price": float(row["retail_price"]),
            }
            cache["stops"].append(entry)
            cache_lookup[key] = entry
            geocoded += 1
            print(f"[{geocoded}/{to_geocode}] {row['name']}, {row['city']}, {row['state']} → ({lat:.4f}, {lon:.4f}) via {method}")
        else:
            skipped += 1
            print(f"  SKIP: {row['name']}, {row['city']}, {row['state']}")

        # Save progress periodically
        if geocoded % save_interval == 0:
            save_cache(cache)
            print(f"  Progress saved ({geocoded} geocoded so far)")

    save_cache(cache)
    print(f"\nDone! Geocoded: {geocoded}, Skipped: {skipped}")
    print(f"Cache saved to: {CACHE_PATH}")


if __name__ == "__main__":
    main()
