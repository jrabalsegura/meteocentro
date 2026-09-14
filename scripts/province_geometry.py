"""Small WGS84 point-in-polygon helper for phase-0 source validation."""

import gzip
import json
from pathlib import Path


def load_full(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def bounds(ring):
    return (min(point[0] for point in ring), min(point[1] for point in ring),
            max(point[0] for point in ring), max(point[1] for point in ring))


def in_bounds(lon, lat, bbox):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def in_ring(lon, lat, ring):
    inside = False
    for a, b in zip(ring, ring[1:]):
        cross = (lon - a[0]) * (b[1] - a[1]) - (lat - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-11 and min(a[0], b[0]) <= lon <= max(a[0], b[0]) and min(a[1], b[1]) <= lat <= max(a[1], b[1]):
            return True
        if (a[1] > lat) != (b[1] > lat):
            crossing = a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if lon < crossing:
                inside = not inside
    return inside


def classify(lon: float, lat: float, collection: dict) -> str | None:
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("coordinate outside WGS84 range")
    matches = []
    for feature in collection["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            if in_bounds(lon, lat, bounds(polygon[0])) and in_ring(lon, lat, polygon[0]) and not any(
                in_bounds(lon, lat, bounds(hole)) and in_ring(lon, lat, hole) for hole in polygon[1:]
            ):
                matches.append(feature["properties"]["province_code"])
                break
    return min(matches) if matches else None


def parse_aemet_dms(value: str) -> float:
    """Parse AEMET inventory coordinates such as 394924N and 025309E."""
    hemisphere = value[-1].upper()
    digits = value[:-1]
    if hemisphere not in "NSEW" or len(digits) not in (6, 7) or not digits.isdigit():
        raise ValueError("invalid AEMET DMS coordinate")
    degrees = int(digits[:-4])
    minutes = int(digits[-4:-2])
    seconds = int(digits[-2:])
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid AEMET DMS minutes or seconds")
    coordinate = degrees + minutes / 60 + seconds / 3600
    return -coordinate if hemisphere in "SW" else coordinate
