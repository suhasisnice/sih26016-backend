"""Approximate real bounding boxes for the four chosen districts, so parcel
coordinates land inside actual Karnataka geography rather than the ocean.
(lat_min, lat_max, lon_min, lon_max) — good enough for a demo map, not
survey-grade."""

import random

DISTRICT_BOUNDS = {
    "Bengaluru Rural": (13.05, 13.35, 77.35, 77.75),
    "Tumakuru": (13.10, 13.60, 76.90, 77.30),
    "Ramanagara": (12.60, 12.90, 77.10, 77.45),
    "Kolar": (12.95, 13.35, 78.05, 78.35),
}


def random_point_wkt(district_name: str, rng: random.Random) -> str:
    """Returns an EWKT point PostGIS accepts directly, avoiding a Shapely
    round-trip for what is two random numbers."""
    lat_min, lat_max, lon_min, lon_max = DISTRICT_BOUNDS[district_name]
    lat = rng.uniform(lat_min, lat_max)
    lon = rng.uniform(lon_min, lon_max)
    return f"SRID=4326;POINT({lon} {lat})"
