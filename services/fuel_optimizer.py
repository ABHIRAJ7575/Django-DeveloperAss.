import logging
import numpy as np

logger = logging.getLogger(__name__)

TANK_RANGE = 500.0      # miles
FUEL_EFFICIENCY = 10.0  # mpg


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in miles."""
    R = 3958.8
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1))))


def _sample_polyline(polyline: list, interval_miles: float = 10.0) -> list:
    if not polyline:
        return []
    samples = [tuple(polyline[0])]
    cumulative = last_sample = 0.0
    for i in range(1, len(polyline)):
        prev, curr = polyline[i - 1], polyline[i]
        cumulative += haversine(prev[0], prev[1], curr[0], curr[1])
        if cumulative - last_sample >= interval_miles:
            samples.append((curr[0], curr[1]))
            last_sample = cumulative
    last = tuple(polyline[-1])
    if samples[-1] != last:
        samples.append(last)
    return samples


def _cumulative_distances(polyline: list) -> list:
    dists = [0.0]
    for i in range(1, len(polyline)):
        dists.append(dists[-1] + haversine(
            polyline[i-1][0], polyline[i-1][1],
            polyline[i][0],   polyline[i][1],
        ))
    return dists


def _progress_of_stop(lat, lon, polyline, cum_dists) -> float:
    idx = min(range(len(polyline)),
              key=lambda i: haversine(lat, lon, polyline[i][0], polyline[i][1]))
    return cum_dists[idx]


# ---------------------------------------------------------------------------
# Corridor filtering
# ---------------------------------------------------------------------------

def filter_corridor(polyline: list, fuel_dataset: list, corridor_miles: float = 100.0) -> list:
    if not polyline or not fuel_dataset:
        return []
    samples = _sample_polyline(polyline, interval_miles=10.0)
    return [
        stop for stop in fuel_dataset
        if min(haversine(stop["latitude"], stop["longitude"], sp[0], sp[1])
               for sp in samples) <= corridor_miles
    ]


# ---------------------------------------------------------------------------
# Global nearest-stop fallback
# ---------------------------------------------------------------------------

def _get_nearest_global_stations(polyline: list, fuel_dataset: list, n: int = 20) -> list:
    """Return up to n stops nearest to evenly-spaced route points, sorted by distance."""
    total = len(polyline)
    key_indices = sorted({0, total // 4, total // 2, 3 * total // 4, total - 1})
    key_points = [polyline[i] for i in key_indices]

    scored = []
    for stop in fuel_dataset:
        min_dist = min(
            haversine(stop["latitude"], stop["longitude"], pt[0], pt[1])
            for pt in key_points
        )
        scored.append((min_dist, stop["retail_price"], stop))
    scored.sort(key=lambda x: (x[0], x[1]))

    seen, result = set(), []
    for _, _, stop in scored:
        key = (stop["name"], stop["city"], stop["state"])
        if key not in seen:
            seen.add(key)
            result.append(stop)
        if len(result) >= n:
            break
    return result


# ---------------------------------------------------------------------------
# Evenly-spaced stop placement (guaranteed coverage fallback)
# ---------------------------------------------------------------------------

def _place_stops_evenly(
    polyline: list,
    cum_dists: list,
    fuel_dataset: list,
    distance_miles: float,
) -> tuple[list, float]:
    """
    When greedy selection fails to cover the route, place stops at every
    ~400-mile mark along the polyline and pick the nearest available station.
    Guarantees at least ceil(distance / TANK_RANGE) stops for long routes.
    """
    stops_needed = max(1, int(np.ceil(distance_miles / TANK_RANGE)))
    interval = distance_miles / (stops_needed + 1)

    selected = []
    total_cost = 0.0
    prev_dist_mark = 0.0

    for i in range(1, stops_needed + 1):
        target_dist = interval * i
        # Find polyline point closest to this distance mark
        idx = min(range(len(cum_dists)), key=lambda j: abs(cum_dists[j] - target_dist))
        target_lat, target_lon = polyline[idx]

        # Pick nearest station to this point
        nearest = min(
            fuel_dataset,
            key=lambda s: haversine(target_lat, target_lon, s["latitude"], s["longitude"])
        )
        segment_miles = target_dist - prev_dist_mark
        total_cost += (segment_miles / FUEL_EFFICIENCY) * nearest["retail_price"]
        selected.append(nearest)
        prev_dist_mark = target_dist

    # Final segment cost
    if selected:
        final_miles = distance_miles - prev_dist_mark
        total_cost += (final_miles / FUEL_EFFICIENCY) * selected[-1]["retail_price"]

    logger.warning(
        "EVENLY-SPACED FALLBACK: placed %d stops for %.1f mile route",
        len(selected), distance_miles,
    )
    return selected, round(total_cost, 2)


# ---------------------------------------------------------------------------
# Main optimizer
# ---------------------------------------------------------------------------

def optimize(polyline: list, distance_miles: float, fuel_dataset: list) -> dict:
    """
    Greedy fuel stop optimizer with multi-layer fallback.
    NEVER returns empty stops or $0 cost for non-trivial routes.
    """
    logger.info(
        "optimize: %d dataset stops, route %.1f miles",
        len(fuel_dataset) if fuel_dataset else 0,
        distance_miles,
    )

    # ── Guard: empty dataset ──────────────────────────────────────────────
    if not fuel_dataset or not polyline:
        logger.error("Empty dataset or polyline — cannot optimize")
        return {"fuel_stops": [], "total_fuel_cost": 0.0}

    # ── Step 1: corridor filter with auto-widening ────────────────────────
    corridor_stops: list = []
    for width in (100.0, 200.0, 350.0, 600.0):
        corridor_stops = filter_corridor(polyline, fuel_dataset, corridor_miles=width)
        if corridor_stops:
            logger.info("Corridor %.0f miles: %d stops found", width, len(corridor_stops))
            break

    # ── Step 2: global nearest fallback ──────────────────────────────────
    if not corridor_stops:
        corridor_stops = _get_nearest_global_stations(polyline, fuel_dataset, n=20)
        logger.warning("FALLBACK: using %d nearest global stops", len(corridor_stops))

    if not corridor_stops:
        logger.error("No stops available at all")
        return {"fuel_stops": [], "total_fuel_cost": 0.0}

    # ── Step 3: greedy selection using route cumulative distance ──────────
    cum_dists = _cumulative_distances(polyline)
    total_route_dist = cum_dists[-1]

    # Use actual route cumulative distance for progress, not straight-line haversine
    stop_progress = {
        id(s): _progress_of_stop(s["latitude"], s["longitude"], polyline, cum_dists)
        for s in corridor_stops
    }

    current_lat, current_lon = polyline[0][0], polyline[0][1]
    current_route_dist = 0.0   # how far along the route we are (miles)
    remaining_range = TANK_RANGE
    selected: list = []
    total_cost = 0.0

    for _ in range(len(corridor_stops) + 20):
        # Remaining route distance from current position
        remaining_route = total_route_dist - current_route_dist

        logger.debug(
            "  pos=%.1f/%.1f mi, remaining_range=%.1f, remaining_route=%.1f",
            current_route_dist, total_route_dist, remaining_range, remaining_route,
        )

        if remaining_route <= remaining_range:
            # Can reach the end on current tank
            if selected:
                total_cost += (remaining_route / FUEL_EFFICIENCY) * selected[-1]["retail_price"]
            break

        # Candidates: ahead of current route position and within tank range
        candidates = []
        for s in corridor_stops:
            prog = stop_progress[id(s)]
            if prog <= current_route_dist + 1.0:
                continue  # behind us
            direct_dist = haversine(current_lat, current_lon, s["latitude"], s["longitude"])
            if direct_dist <= remaining_range:
                candidates.append((direct_dist, s))

        # Fallback A: any reachable stop ignoring direction
        if not candidates:
            logger.debug("  Fallback A: ignoring direction constraint")
            for s in corridor_stops:
                direct_dist = haversine(current_lat, current_lon, s["latitude"], s["longitude"])
                if 0.1 < direct_dist <= remaining_range:
                    candidates.append((direct_dist, s))

        # Fallback B: nearest stop in full dataset
        if not candidates:
            logger.warning("  Fallback B: picking nearest from full dataset")
            nearest = min(
                fuel_dataset,
                key=lambda s: haversine(current_lat, current_lon, s["latitude"], s["longitude"])
            )
            d = haversine(current_lat, current_lon, nearest["latitude"], nearest["longitude"])
            candidates = [(d, nearest)]

        best_stop = min(candidates, key=lambda x: x[1]["retail_price"])[1]
        best_dist = haversine(current_lat, current_lon,
                              best_stop["latitude"], best_stop["longitude"])

        total_cost += (best_dist / FUEL_EFFICIENCY) * best_stop["retail_price"]
        selected.append(best_stop)

        current_lat = best_stop["latitude"]
        current_lon = best_stop["longitude"]
        # Advance route progress to this stop's position
        current_route_dist = max(current_route_dist + best_dist,
                                 stop_progress.get(id(best_stop), current_route_dist))
        remaining_range = TANK_RANGE  # refuel to full

    # ── Step 4: validate result — use evenly-spaced fallback if needed ────
    stops_needed = max(1, int(np.ceil(distance_miles / TANK_RANGE)))

    if not selected or (distance_miles > TANK_RANGE and len(selected) < stops_needed):
        logger.warning(
            "Greedy result insufficient (%d stops for %.1f mi) — using evenly-spaced fallback",
            len(selected), distance_miles,
        )
        selected, total_cost = _place_stops_evenly(
            polyline, cum_dists, fuel_dataset, distance_miles
        )

    # ── Step 5: short-route — always return at least 1 stop ──────────────
    if not selected:
        mid_lat = (polyline[0][0] + polyline[-1][0]) / 2
        mid_lon = (polyline[0][1] + polyline[-1][1]) / 2
        nearest = min(corridor_stops,
                      key=lambda s: haversine(mid_lat, mid_lon, s["latitude"], s["longitude"]))
        selected = [nearest]
        total_cost = round((distance_miles / FUEL_EFFICIENCY) * nearest["retail_price"], 2)
        logger.info("Short-route fallback: 1 stop at %s, %s", nearest["city"], nearest["state"])

    logger.info(
        "RESULT: %d stops, total_distance=%.1f mi, total_cost=$%.2f",
        len(selected), distance_miles, total_cost,
    )

    return {
        "fuel_stops": selected,
        "total_fuel_cost": round(total_cost, 2),
    }
