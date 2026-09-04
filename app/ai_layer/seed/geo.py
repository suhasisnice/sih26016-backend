"""Where seeded parcels sit, and what shape they are.

**How a parcel gets its position.** It is anchored on the case's VILLAGE, not
scattered inside the district. Villages here are real taluk towns with real
coordinates, and a case's parcels are laid out as a block or a corridor on
the farmland a few kilometres outside one. Two reasons, one modelling and one
cosmetic:

- A case already carries a village_id, and an acquisition happens in that
  village. Placing its parcels at an unrelated random point in a 40 km
  district box meant the geometry contradicted the row it hung off.
- A district box is mostly not a town, and a uniform sample inside one lands
  in reservoirs. Before this, parcels were drawn on open water — invisible on
  the old illustrated canvas, glaring the moment there was a real basemap.

Anchoring on a settlement makes land the default rather than the lucky case:
a town is on land by definition, and its outskirts are farmland. WATER is the
backstop for the rest — candidate positions inside a known water body are
rejected and resampled.

**On the polygons.** A seeded parcel's boundary is generated, not surveyed —
exactly as generated as its survey number, its owner and its award amount,
which is to say the whole seed. What matters is that it is generated ONCE and
stored, so everything downstream reads the same shape: PostGIS measures it,
the map draws it, and a field officer can replace it with a real survey
without anything else changing. The previous build drew a decorative outline
in the browser at render time, which meant the shape existed nowhere, matched
nothing, and could not be queried.

The generator scales every polygon to its parcel's declared `area_ha`, so
`ST_Area(boundary::geography) / 10000` comes back equal to `area_ha` to
within a rounding error. That invariant is the point: the map draws the same
hectares the dashboard totals.
"""

import math
import random
import zlib

# Real coordinates for the sixteen Karnataka taluk towns in
# reference.DISTRICT_VILLAGES. These are public record, and they are what
# makes "the parcels are near the village named on the case" true rather than
# decorative.
VILLAGE_ANCHORS = {
    # Bengaluru Rural
    "Devanahalli": (13.2437, 77.7118),
    "Doddaballapura": (13.2957, 77.5378),
    "Hoskote": (13.0707, 77.7979),
    "Nelamangala": (13.0996, 77.3946),
    # Tumakuru
    "Tumakuru": (13.3379, 77.1173),
    "Sira": (13.7411, 76.9040),
    "Madhugiri": (13.6620, 77.2100),
    "Koratagere": (13.5220, 77.2380),
    # Ramanagara
    "Ramanagara": (12.7209, 77.2800),
    "Channapatna": (12.6514, 77.2065),
    "Kanakapura": (12.5462, 77.4200),
    "Magadi": (12.9575, 77.2248),
    # Kolar
    "Kolar": (13.1357, 78.1325),
    "Malur": (13.0038, 77.9370),
    "Mulbagal": (13.1650, 78.3930),
    "Bangarpet": (12.9915, 78.1780),
}

# The secondary states' villages are generated as "<District> Block 1/2"
# rather than named, so they anchor on the district town and step out from it.
# Real coordinates again — the block names are synthetic, the geography is not.
DISTRICT_ANCHORS = {
    "Pune": (18.5204, 73.8567),
    "Nashik": (19.9975, 73.7898),
    "Coimbatore": (11.0168, 76.9558),
    "Madurai": (9.9252, 78.1198),
    "Surat": (21.1702, 72.8311),
    "Rajkot": (22.3039, 70.8022),
}

# Somewhere obviously wrong in central India, so a village that reaches here
# shows up as a misplaced parcel to investigate rather than hiding among the
# real ones.
FALLBACK_ANCHOR = (22.75, 78.75)

# Water bodies big enough to swallow a parcel, as (lat, lon, radius_m).
# Rejection zones, not a landmask: the village anchoring above is what keeps
# sites on farmland, and this only has to catch the tanks and reservoirs close
# enough to one of those towns to fall inside SITE_OFFSET_M.
#
# The first group is MEASURED. A seeded parcel landed in each one, and the
# centre and radius were read off the OpenStreetMap raster by walking outward
# on sixteen bearings until the water pixels stopped — not estimated from
# memory. Radii carry 250 m of margin and are rounded up to 50 m.
#
# The second group is DEFENSIVE: large reservoirs near the anchor towns that
# nothing has landed in yet. They are approximate, and they are here so that a
# reseed with different dates cannot put a corridor in one. Anything that does
# get observed should be measured and promoted to the first group.
#
# Grow this list the same way: seed, run the basemap check, measure, add.
WATER = [
    # --- measured off the basemap ---
    (13.0816, 77.7645, 1_350),   # Hoskote lake
    (13.0469, 77.7781, 300),     # tank south of Hoskote
    (12.6347, 77.1929, 1_200),   # tank west of Channapatna
    (13.6837, 77.2281, 1_250),   # tank at Madhugiri
    (13.3160, 77.5695, 950),     # tank north of Doddaballapura
    (9.9588, 78.0520, 1_400),    # tank north-west of Madurai
    (9.8553, 78.1492, 1_200),    # tank south of Madurai
    (13.1043, 78.1262, 400),     # tank south of Kolar
    (13.1528, 78.1322, 950),     # tank north of Kolar
    (12.6607, 77.1674, 1_300),   # second tank west of Channapatna
    (20.0267, 73.7260, 700),     # Godavari backwater, Nashik
    (9.9699, 78.0310, 1_100),    # second tank north-west of Madurai
    (9.8823, 78.1859, 1_850),    # tank east of Madurai
    # --- defensive, approximate ---
    (13.1400, 77.4800, 3_500),   # Hesaraghatta lake
    (12.9600, 77.3500, 3_000),   # Thippagondanahalli reservoir
    (18.4400, 73.7700, 3_500),   # Khadakwasla reservoir, Pune
    (20.0300, 73.6800, 3_000),   # Gangapur dam, Nashik
]

# How far outside the settlement an acquisition sits. Close enough to belong
# to the village, far enough out to be farmland rather than the town itself —
# nobody notifies the middle of a taluk headquarters for a highway corridor.
SITE_OFFSET_M = (1_500, 5_000)

# Metres per degree. Latitude is near enough constant; longitude shrinks with
# the cosine of latitude, which at 13°N (Karnataka) is a 2.5% correction and
# at 22°N (Gujarat) is 7%. Small, but it is the difference between a parcel
# that is square on the map and one visibly stretched east-west.
METRES_PER_DEG_LAT = 110_574.0
METRES_PER_DEG_LON_EQUATOR = 111_320.0

# How far apart parcels in one case sit, in metres. Real acquisitions are
# contiguous — a road corridor or a single block — so the parcels of one case
# are laid out touching rather than scattered across the district. Scattering
# them was the old behaviour and it made "click a project and see its plots"
# meaningless: the plots were 40 km apart.
PARCEL_PITCH_M = 240.0


def _metres_per_deg_lon(lat: float) -> float:
    return METRES_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))


def rng_for(key: str) -> random.Random:
    """A generator private to one case, derived from a stable key.

    Geometry deliberately does NOT draw from the seed's shared rng. Rejection
    sampling consumes a variable number of values, so with a shared generator
    every exclusion added to WATER reshuffled the draws of every case after
    it: fixing one parcel in a lake moved a dozen others into new ones, and
    the list could never converge. Off a per-case generator, adding an
    exclusion perturbs only the case that needed it.

    crc32 rather than hash(): Python salts string hashing per process, so
    hash() would place parcels somewhere new on every run.
    """
    return random.Random(zlib.crc32(key.encode("utf-8")))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Used only against the water list, at
    scales of a few kilometres, but written properly because a flat
    approximation that is fine in Karnataka is not fine in Gujarat."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def on_land(lat: float, lon: float) -> bool:
    """False when the point falls in one of the known water bodies.

    Deliberately named for what callers want to know rather than for what it
    checks. It is a rejection list and not a landmask — it cannot know about a
    pond nobody has added — so it is a backstop behind the village anchoring,
    not the thing keeping parcels out of the sea.
    """
    return all(
        haversine_m(lat, lon, w_lat, w_lon) > radius for w_lat, w_lon, radius in WATER
    )


def village_anchor(village_name: str, district_name: str) -> tuple[float, float]:
    """The settlement a case's parcels belong to, as (lat, lon).

    Named villages resolve directly. The secondary states' synthetic
    "<District> Block N" villages step out from the district town on a bearing
    derived from N, so the two blocks of a district do not sit on top of each
    other.
    """
    if village_name in VILLAGE_ANCHORS:
        return VILLAGE_ANCHORS[village_name]

    district_lat, district_lon = DISTRICT_ANCHORS.get(district_name, FALLBACK_ANCHOR)
    # "Pune Block 2" -> 2. Any village that is not a named anchor and not a
    # numbered block sits on the district town itself, which is still land.
    trailing = village_name.rsplit(" ", 1)[-1]
    index = int(trailing) if trailing.isdigit() else 0
    if index == 0:
        return (district_lat, district_lon)

    bearing = (index * 2.399963)  # golden angle, so blocks spread rather than stack
    distance = 9_000.0
    return (
        district_lat + (distance * math.cos(bearing)) / METRES_PER_DEG_LAT,
        district_lon + (distance * math.sin(bearing)) / _metres_per_deg_lon(district_lat),
    )


def case_site(
    village_name: str,
    district_name: str,
    rng: random.Random,
) -> tuple[float, float]:
    """Where one case's acquisition happens, as (lat, lon).

    On the farmland ring around the case's own village. Resampled while the
    draw lands in water, and falling back to the village itself — a town, so
    unambiguously land — rather than looping forever if a village is ringed by
    reservoir on every bearing.
    """
    anchor_lat, anchor_lon = village_anchor(village_name, district_name)
    metres_per_deg_lon = _metres_per_deg_lon(anchor_lat)

    for _ in range(24):
        bearing = rng.uniform(0, 2 * math.pi)
        distance = rng.uniform(*SITE_OFFSET_M)
        lat = anchor_lat + (distance * math.cos(bearing)) / METRES_PER_DEG_LAT
        lon = anchor_lon + (distance * math.sin(bearing)) / metres_per_deg_lon
        if on_land(lat, lon):
            return (lat, lon)

    return (anchor_lat, anchor_lon)


def parcel_positions(
    centre_lat: float,
    centre_lon: float,
    count: int,
    rng: random.Random,
) -> list[tuple[float, float]]:
    """Lay `count` parcels out around a case's site, as [(lat, lon), ...].

    Two layouts, chosen per case, because the two shapes of land acquisition
    look completely different on a map and having only one would make every
    project on the national view look identical:

    - **corridor** — a road, canal or transmission line. Parcels run in a
      line along a bearing, which is what most linear infrastructure
      acquisition actually looks like.
    - **block** — an industrial park, a plant, a township. Parcels fill a
      compact grid.

    Jitter is applied either way. A perfectly regular lattice reads as
    generated data at a glance, and the point of a demo map is that it does
    not.
    """
    metres_per_deg_lon = _metres_per_deg_lon(centre_lat)

    def offset(east_m: float, north_m: float) -> tuple[float, float]:
        return (
            centre_lat + north_m / METRES_PER_DEG_LAT,
            centre_lon + east_m / metres_per_deg_lon,
        )

    # Corridors for roughly two cases in five.
    if rng.random() < 0.4:
        bearing = rng.uniform(0, math.pi)
        along_e, along_n = math.sin(bearing), math.cos(bearing)
        # Perpendicular, so a corridor can be two parcels wide where the
        # alignment cuts across a wider holding.
        across_e, across_n = math.cos(bearing), -math.sin(bearing)
        positions = []
        for index in range(count):
            along = (index - (count - 1) / 2) * PARCEL_PITCH_M
            across = (index % 2 - 0.5) * PARCEL_PITCH_M * 0.6
            east = along * along_e + across * across_e + rng.uniform(-40, 40)
            north = along * along_n + across * across_n + rng.uniform(-40, 40)
            positions.append(offset(east, north))
        return positions

        return _keep_on_land(positions, centre_lat, centre_lon)

    columns = max(1, round(math.sqrt(count)))
    rows = math.ceil(count / columns)
    positions = []
    for index in range(count):
        column = index % columns
        row = index // columns
        east = (column - (columns - 1) / 2) * PARCEL_PITCH_M + rng.uniform(-50, 50)
        north = (row - (rows - 1) / 2) * PARCEL_PITCH_M + rng.uniform(-50, 50)
        positions.append(offset(east, north))
    return _keep_on_land(positions, centre_lat, centre_lon)


def _keep_on_land(
    positions: list[tuple[float, float]],
    centre_lat: float,
    centre_lon: float,
) -> list[tuple[float, float]]:
    """Pull any parcel that landed in water back across the site.

    The case site is already checked, but a block half a kilometre wide can
    still put its far edge in a reservoir the centre cleared. Reflecting the
    offending parcel through the centre keeps it the same distance out and in
    the same layout, just on the other side — which preserves the corridor or
    grid rather than dropping a parcel into the middle of it.
    """
    fixed = []
    for lat, lon in positions:
        if on_land(lat, lon):
            fixed.append((lat, lon))
            continue
        mirrored = (2 * centre_lat - lat, 2 * centre_lon - lon)
        fixed.append(mirrored if on_land(*mirrored) else (centre_lat, centre_lon))
    return fixed


def parcel_polygon_wkt(lat: float, lon: float, area_ha: float, rng: random.Random) -> str:
    """A closed, irregular parcel boundary of exactly `area_ha`, as EWKT.

    Built in metres and then converted to degrees, which is the only way the
    area comes out right: a polygon laid out directly in degrees is stretched
    by the longitude convergence and its true area is wrong by the cosine of
    the latitude.

    The shape is an irregular convex polygon of four to six sides — the
    silhouette of a real revenue parcel far more often than a rectangle is.
    It is scaled by sqrt(target / measured) after the fact rather than
    constructed to size, because scaling a shoelace area is exact and solving
    for the radii that produce a given area is not.

    Vertices come out counterclockwise (angles ascending in an x-east,
    y-north frame), which is the winding RFC 7946 asks of a GeoJSON exterior
    ring. PostGIS does not care; anything downstream that follows the spec
    strictly will.
    """
    sides = rng.randint(4, 6)

    # Angles spread around the circle with jitter, kept sorted so the ring
    # never self-intersects. An unsorted ring produces a bow-tie, which has a
    # nonsense shoelace area and would fail ST_IsValid.
    step = 2 * math.pi / sides
    angles = sorted(index * step + rng.uniform(-step * 0.3, step * 0.3) for index in range(sides))
    radii = [rng.uniform(0.72, 1.28) for _ in range(sides)]
    points = [(r * math.cos(a), r * math.sin(a)) for a, r in zip(angles, radii)]

    # Shoelace, in whatever units the unit-radius shape came out in.
    twice_area = sum(
        points[i][0] * points[(i + 1) % sides][1] - points[(i + 1) % sides][0] * points[i][1]
        for i in range(sides)
    )
    unit_area = abs(twice_area) / 2
    if unit_area <= 0:  # degenerate; unreachable with sorted angles, but cheap to guard
        unit_area = 1.0

    scale = math.sqrt((area_ha * 10_000.0) / unit_area)
    metres_per_deg_lon = _metres_per_deg_lon(lat)

    ring = []
    for x, y in points:
        ring.append(
            (
                lon + (x * scale) / metres_per_deg_lon,
                lat + (y * scale) / METRES_PER_DEG_LAT,
            )
        )
    ring.append(ring[0])  # a polygon ring must close

    coordinates = ", ".join(f"{x:.7f} {y:.7f}" for x, y in ring)
    return f"SRID=4326;POLYGON(({coordinates}))"
