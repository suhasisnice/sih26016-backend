"""Approximate real bounding boxes for every seeded district, so parcel
coordinates land inside actual Indian geography rather than the ocean.

(lat_min, lat_max, lon_min, lon_max) — good enough for a demo map, not
survey-grade.

The four Karnataka districts are the demo's focus. The rest exist so the
national rollup and the state filter have real geography behind them: a map
that zooms out to "India" and shows four clustered districts is not showing a
national platform.
"""

import random

DISTRICT_BOUNDS = {
    # Karnataka
    "Bengaluru Rural": (13.05, 13.35, 77.35, 77.75),
    "Tumakuru": (13.10, 13.60, 76.90, 77.30),
    "Ramanagara": (12.60, 12.90, 77.10, 77.45),
    "Kolar": (12.95, 13.35, 78.05, 78.35),
    # Maharashtra
    "Pune": (18.35, 18.75, 73.65, 74.15),
    "Nashik": (19.85, 20.25, 73.60, 74.10),
    # Tamil Nadu
    "Coimbatore": (10.85, 11.25, 76.75, 77.15),
    "Madurai": (9.75, 10.15, 77.90, 78.30),
    # Gujarat
    "Surat": (21.05, 21.35, 72.70, 73.10),
    "Rajkot": (22.15, 22.50, 70.60, 71.00),
}

# Used when a district has no entry above. Centred on the country rather than
# on Karnataka, so a missing bound is visible as an obviously wrong parcel in
# central India instead of hiding among the real ones.
FALLBACK_BOUNDS = (22.50, 23.00, 78.50, 79.00)


def random_point_wkt(district_name: str, rng: random.Random) -> str:
    """Returns an EWKT point PostGIS accepts directly, avoiding a Shapely
    round-trip for what is two random numbers."""
    lat_min, lat_max, lon_min, lon_max = DISTRICT_BOUNDS.get(district_name, FALLBACK_BOUNDS)
    lat = rng.uniform(lat_min, lat_max)
    lon = rng.uniform(lon_min, lon_max)
    return f"SRID=4326;POINT({lon} {lat})"
