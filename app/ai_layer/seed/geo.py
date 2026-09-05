"""Coordinate generation, kept to one place so every parcel and every
district centroid comes from the same bounding boxes.

Boxes are hand-picked to sit inside each district's real extent -- coarse,
but enough that the map never scatters a parcel across the ocean, which is
the actual failure mode this guards against.
"""

import random

# (lat_min, lat_max, lon_min, lon_max)
DISTRICT_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "Bengaluru Rural": (12.95, 13.35, 77.35, 77.75),
    "Tumakuru": (13.15, 13.60, 76.90, 77.40),
    "Ramanagara": (12.60, 12.90, 77.05, 77.45),
    "Kolar": (13.00, 13.30, 78.05, 78.45),
    "Pune": (18.35, 18.70, 73.60, 74.00),
    "Nashik": (19.80, 20.10, 73.60, 73.90),
    "Coimbatore": (10.90, 11.20, 76.90, 77.10),
    "Madurai": (9.80, 10.00, 78.00, 78.20),
    "Surat": (21.10, 21.30, 72.70, 72.90),
    "Rajkot": (22.20, 22.40, 70.70, 70.90),
}


def random_point(rng: random.Random, district_name: str) -> tuple[float, float]:
    """A (lat, lon) fix inside the district's box."""
    lat_min, lat_max, lon_min, lon_max = DISTRICT_BOUNDS[district_name]
    return round(rng.uniform(lat_min, lat_max), 6), round(rng.uniform(lon_min, lon_max), 6)


def point_ewkt(lat: float, lon: float) -> str:
    return f"SRID=4326;POINT({lon} {lat})"


def small_square_ewkt(lat: float, lon: float, half_side_deg: float = 0.0012) -> str:
    """A tiny closed square around (lat, lon), standing in for a surveyed
    boundary. Not a real cadastral outline -- there is none to seed from --
    but it renders as a plausible parcel footprint on the map."""
    ring = [
        (lon - half_side_deg, lat - half_side_deg),
        (lon + half_side_deg, lat - half_side_deg),
        (lon + half_side_deg, lat + half_side_deg),
        (lon - half_side_deg, lat + half_side_deg),
        (lon - half_side_deg, lat - half_side_deg),
    ]
    points = ", ".join(f"{x} {y}" for x, y in ring)
    return f"SRID=4326;POLYGON(({points}))"
