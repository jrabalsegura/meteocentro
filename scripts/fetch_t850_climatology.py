"""Daily 850 hPa temperature climatology (1991-2020) for the forecast places.

Source: NCEP/NCAR Reanalysis 1, daily long-term mean published by NOAA PSL (public
domain), read through its documented OPeNDAP service. Only the four 2.5° grid points
around each place are requested. The result is a reference line for the ensemble chart;
it is static and is regenerated only by running this script again.

    python3 scripts/fetch_t850_climatology.py
"""

import hashlib
import json
import math
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DATASET = (
    "https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.derived/pressure/"
    "air.day.ltm.1991-2020.nc"
)
LEVEL_INDEX = 2  # 1000, 925, 850 hPa ...
PLACES = {"madrid": (40.4165, -3.70256), "huetor-santillan": (37.22091, -3.51634)}
SMOOTH_DAYS = 11
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend/src/meteocentro/reference/t850_climatology.json"


def fetch(url):
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read().decode("ascii")


def corners(latitude, longitude):
    """Indices of the surrounding grid: lat 90 → -90 and lon 0 → 357.5, both every 2.5°."""
    east = longitude % 360
    lat_i = math.floor((90 - latitude) / 2.5)
    lon_i = math.floor(east / 2.5)
    return lat_i, lon_i, (90 - lat_i * 2.5 - latitude) / 2.5, (east - lon_i * 2.5) / 2.5


def grid_series(lat_i, lon_i):
    url = f"{DATASET}.ascii?air[0:364][{LEVEL_INDEX}][{lat_i}:{lat_i + 1}][{lon_i}:{lon_i + 1}]"
    text = fetch(url)
    values = {}
    for day, _level, row, first, second in re.findall(
        r"^\[(\d+)\]\[(\d+)\]\[(\d+)\], ([-\d.eE]+), ([-\d.eE]+)$", text, re.M
    ):
        values[(int(day), int(row))] = (float(first), float(second))
    if len(values) != 365 * 2:
        raise SystemExit("Unexpected OPeNDAP response")
    return url, values


def smooth(values, days):
    half = days // 2
    return [
        sum(values[(i + k) % len(values)] for k in range(-half, half + 1)) / days
        for i in range(len(values))
    ]


def main():
    places = {}
    for code, (latitude, longitude) in PLACES.items():
        lat_i, lon_i, north_weight, east_weight = corners(latitude, longitude)
        url, grid = grid_series(lat_i, lon_i)
        daily = []
        for day in range(365):
            (nw, ne), (sw, se) = grid[(day, 0)], grid[(day, 1)]
            north = nw * (1 - east_weight) + ne * east_weight
            south = sw * (1 - east_weight) + se * east_weight
            # Row 0 is the northern latitude; the weight grows towards the south.
            kelvin = north * (1 - north_weight) + south * north_weight
            daily.append(kelvin - 273.15)
        places[code] = {
            "latitude": latitude,
            "longitude": longitude,
            "grid_indices": {"lat": [lat_i, lat_i + 1], "lon": [lon_i, lon_i + 1]},
            "url": url,
            "values": [round(v, 2) for v in smooth(daily, SMOOTH_DAYS)],
        }
    document = {
        "variable": "temperature_850hPa",
        "unit": "°C",
        "period": "1991-2020",
        "dataset": "NCEP/NCAR Reanalysis 1, daily long-term mean (NOAA PSL)",
        "reference": "https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html",
        "licence": "Public domain (NOAA); cite NCEP/NCAR Reanalysis 1 and NOAA PSL",
        "method": (
            "Bilinear interpolation of the four surrounding 2.5° grid points; "
            f"{SMOOTH_DAYS}-day centred circular running mean. 365 values from 1 January; "
            "29 February uses 28 February."
        ),
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "places": places,
    }
    body = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(body, encoding="utf-8")
    print(OUTPUT, hashlib.sha256(body.encode()).hexdigest())


if __name__ == "__main__":
    main()
