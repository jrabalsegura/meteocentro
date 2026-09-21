import gzip
import json
import os
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape

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
