"""Fetch four official IGN province polygons and derive a display-only simplification."""

import gzip
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api-features.ign.es/collections/administrativeunit/items"
PROVINCES = {"05": "Ávila", "19": "Guadalajara", "28": "Madrid", "40": "Segovia"}
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


def distance_squared(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2
    t = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
    return (point[0] - start[0] - t * dx) ** 2 + (point[1] - start[1] - t * dy) ** 2


def simplify_ring(ring, tolerance=0.0002):
    points = ring[:-1] if ring[0] == ring[-1] else ring
    if len(points) < 5:
        return ring
    # Keep the most distant vertex as a split point so a closed ring cannot collapse.
    split = max(range(1, len(points)), key=lambda i: distance_squared(points[i], points[0], points[-1]))
    keep = {0, split, len(points) - 1}
    stack = [(0, split), (split, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        index = max(range(start + 1, end), key=lambda i: distance_squared(points[i], points[start], points[end]))
        if distance_squared(points[index], points[start], points[end]) > tolerance * tolerance:
            keep.add(index)
            stack.extend(((start, index), (index, end)))
    result = [points[i] for i in sorted(keep)]
    return result + [result[0]] if len(result) >= 3 else ring


def simplify_geometry(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    simplified = [[simplify_ring(ring) for ring in polygon] for polygon in polygons]
    return {"type": geometry["type"], "coordinates": simplified[0] if geometry["type"] == "Polygon" else simplified}


def main():
    CONFIG.mkdir(exist_ok=True)
    features = []
    source = []
    for code, name in PROVINCES.items():
        query = urllib.parse.urlencode({"nameunit": name, "nationallevelname": "Provincia", "limit": 2, "f": "json"})
        url = BASE + "?" + query
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Meteocentro phase-0/0.1"}), timeout=45) as response:
            document = json.load(response)
        matches = document.get("features", [])
        if len(matches) != 1 or document.get("numberMatched") != 1:
            raise ValueError(f"Expected one official polygon for {name}; got {len(matches)}")
        feature = matches[0]
        if feature["properties"].get("nameunit") != name or feature["properties"].get("nationallevelname") != "Provincia":
            raise ValueError(f"Unexpected IGN feature for {name}")
        if feature["geometry"]["type"] not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"Unexpected geometry for {name}")
        features.append({"type": "Feature", "properties": {"province_code": code, "name": name,
                         "ign_nationalcode": feature["properties"]["nationalcode"]}, "geometry": feature["geometry"]})
        source.append({"province_code": code, "name": name, "url": url, "ign_feature_id": feature.get("id")})
    full = {"type": "FeatureCollection", "features": features}
    raw = json.dumps(full, ensure_ascii=False, separators=(",", ":")).encode()
    full_path = CONFIG / "provinces-full.geojson.gz"
    with gzip.open(full_path, "wb", compresslevel=9) as destination:
        destination.write(raw)
    display = {"type": "FeatureCollection", "features": [
        {**feature, "geometry": simplify_geometry(feature["geometry"])} for feature in features
    ]}
    display_path = CONFIG / "provinces.geojson"
    display_path.write_text(json.dumps(display, ensure_ascii=False, separators=(",", ":")) + "\n")
    provenance = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "source": source,
                  "full_sha256": hashlib.sha256(raw).hexdigest(), "display_tolerance_degrees": 0.0002,
                  "classification_file": full_path.name, "display_file": display_path.name,
                  "license": "IGN/CNIG BDLJE CC-BY 4.0; derived work attribution required"}
    (CONFIG / "provinces.provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"province_count": len(features), "full_bytes": len(raw),
                      "display_bytes": display_path.stat().st_size,
                      "codes": [feature["properties"]["province_code"] for feature in features]}))


if __name__ == "__main__":
    main()
