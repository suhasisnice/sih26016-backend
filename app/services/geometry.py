"""Building PostGIS EWKT literals from points a client sent, the write
counterpart to app.ai_layer.seed.geo's read-side helpers of the same shape.
"""

from app.schemas.survey import LatLng


def point_ewkt(lat: float, lon: float) -> str:
    return f"SRID=4326;POINT({lon} {lat})"


def polygon_ewkt(points: list[LatLng]) -> str:
    """A closed polygon ring from the corners a field officer walked and
    tapped, in the order they walked them. The caller is responsible for
    requiring at least 3 distinct points — this only closes the ring."""
    ring = [(p.longitude, p.latitude) for p in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    coords = ", ".join(f"{x} {y}" for x, y in ring)
    return f"SRID=4326;POLYGON(({coords}))"
