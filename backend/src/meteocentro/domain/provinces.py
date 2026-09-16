import gzip
import json
import os
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, box, shape

DEFAULT_PATH = Path(
    os.environ.get(
        "PROVINCES_PATH", Path(__file__).resolve().parents[4] / "config/provinces-full.geojson.gz"
    )
)
ALLOWED_CODES = frozenset({"05", "19", "28", "40"})


@lru_cache(maxsize=4)
def load_provinces(path: Path = DEFAULT_PATH):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        collection = json.load(source)
    found = {
        feature["properties"]["province_code"]: shape(feature["geometry"])
        for feature in collection["features"]
    }
    if set(found) != ALLOWED_CODES:
        raise ValueError("province dataset must contain the four expected codes")
    return found


def classify_province(longitude: float, latitude: float, polygons=None) -> str | None:
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError("coordinates outside WGS84 range")
    point = Point(longitude, latitude)
    polygons = polygons or load_provinces()
    matches = [code for code, polygon in polygons.items() if polygon.covers(point)]
    return min(matches) if matches else None


def classify_minute_precision_location(
    longitude: float, latitude: float, polygons=None
) -> str | None:
    """Classify a manually verified location shown only to whole minutes.

    The site's rounding rule is unknown, so allow one full minute in each direction.
    A result is returned only when that entire uncertainty box is inside one province.
    """
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError("coordinates outside WGS84 range")
    minute = 1 / 60
    possible_area = box(
        longitude - minute,
        latitude - minute,
        longitude + minute,
        latitude + minute,
    )
    polygons = polygons or load_provinces()
    matches = [code for code, polygon in polygons.items() if polygon.covers(possible_area)]
    return matches[0] if len(matches) == 1 else None
