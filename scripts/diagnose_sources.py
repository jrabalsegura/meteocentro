"""Limited, read-only phase-0 probe. Never prints credentials, data URLs or raw records."""

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from province_geometry import classify, load_full, parse_aemet_dms

AGENT = "Meteocentro phase-0 feasibility check/0.1"
MAX_BYTES = 8_000_000
AEMET_BASE = "https://opendata.aemet.es/opendata/api/"


def fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("response exceeds diagnostic size limit")
            return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, b""


def decode_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return json.loads(body.decode("iso-8859-1"))


def aemet_product(path: str) -> dict:
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        return {"status": "pending_access", "reason": "AEMET_API_KEY is not set"}
    url = AEMET_BASE + path + "?" + urllib.parse.urlencode({"api_key": key})
    try:
        status, body = fetch(url)
        result = {"first_http": status}
        if status != 200:
            return result
        envelope = decode_json(body)
        result["aemet_estado"] = envelope.get("estado")
        result["envelope_fields"] = sorted(envelope)
        for name in ("datos", "metadatos"):
            target = envelope.get(name)
            if not target:
                continue
            parsed = urllib.parse.urlparse(target)
            if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("aemet.es"):
                result[name + "_error"] = "unexpected data host"
                continue
            data_status, data_body = fetch(target)
            result[name + "_http"] = data_status
            if data_status != 200:
                continue
            if name == "metadatos":
                metadata = decode_json(data_body)
                result["metadata_fields"] = sorted(metadata) if isinstance(metadata, dict) else []
                if isinstance(metadata, dict):
                    fields = metadata.get("campos")
                    if isinstance(fields, list):
                        result["metadata_metric_fields"] = [
                            field for field in fields
                            if isinstance(field, dict) and str(field.get("id", field.get("nombre", ""))).lower()
                            in {"ta", "prec", "pres", "pres_nmar", "pacutp", "fint", "lat", "lon", "vv"}
                        ]
                continue
            records = decode_json(data_body)
            if not isinstance(records, list):
                result["data_type"] = type(records).__name__
                continue
            result["count"] = len(records)
            result["fields"] = sorted({field for record in records for field in record})
            result["sample_ids"] = [record.get("idema", record.get("indicativo")) for record in records[:5]]
            result["provinces"] = dict(Counter(str(record.get("provincia")) for record in records))
            result["target_province_ids"] = {
                province: [record.get("indicativo") for record in records
                           if record.get("provincia") == province][:2]
                for province in ("MADRID", "AVILA", "SEGOVIA", "GUADALAJARA")
            }
            if records:
                result["coordinate_examples"] = {
                    field: records[0].get(field) for field in ("lat", "lon", "latitud", "longitud")
                    if field in records[0]
                }
            result["timestamp_examples"] = sorted({str(record.get("fint", record.get("fecha"))) for record in records})[:3]
            result["null_counts"] = {
                field: sum(record.get(field) is None for record in records)
                for field in result["fields"] if field in {"ta", "prec", "pres", "hr", "vv"}
            }
            full_path = Path(__file__).resolve().parents[1] / "config/provinces-full.geojson.gz"
            if full_path.is_file():
                provinces = load_full(full_path)
                unique = {}
                for record in records:
                    identity = record.get("idema", record.get("indicativo"))
                    if identity in unique:
                        continue
                    try:
                        if "lat" in record and "lon" in record:
                            lon, lat = float(record["lon"]), float(record["lat"])
                        elif "latitud" in record and "longitud" in record:
                            lon = parse_aemet_dms(record["longitud"])
                            lat = parse_aemet_dms(record["latitud"])
                        else:
                            continue
                        unique[identity] = classify(lon, lat, provinces)
                    except (ValueError, TypeError):
                        unique[identity] = "invalid_coordinates"
                result["unique_station_count"] = len(unique)
                result["polygon_counts"] = dict(Counter(code for code in unique.values() if code))
                result["polygon_sample_ids"] = {
                    code: [identity for identity, assigned in unique.items() if assigned == code][:2]
                    for code in ("05", "19", "28", "40")
                }
        return result
    except (urllib.error.URLError, TimeoutError):
        return {"status": "network_error"}
    except (ValueError, json.JSONDecodeError, UnicodeError, KeyError) as error:
        return {"status": "parse_error", "error_type": type(error).__name__}


def meteoclimatic_feed(pattern: str = "ES") -> dict:
    if not pattern.isalnum() or len(pattern) > 24:
        raise ValueError("invalid feed pattern")
    url = "https://www.meteoclimatic.net/feed/xml/" + pattern
    try:
        status, body = fetch(url)
        result = {"http": status}
        if status != 200:
            return result
        root = ET.fromstring(body)
        stations = root.findall(".//station")
        result.update({
            "root": root.tag,
            "version": root.attrib.get("version"),
            "count": len(stations),
            "fields": sorted({child.tag for station in stations for child in station}),
            "sensor_fields": sorted({child.tag for station in stations for child in station.findall(".//stationdata/*")}),
            "sample_ids": [station.findtext("id") for station in stations[:5]],
            "station_pubdate_present": sum(station.findtext("pubDate") is not None for station in stations),
            "coordinate_fields": sorted({tag for tag in ("latitude", "longitude", "lat", "lon") if any(station.find(tag) is not None for station in stations)}),
        })
        return result
    except (urllib.error.URLError, TimeoutError):
        return {"status": "network_error"}
    except (ValueError, ET.ParseError, UnicodeError) as error:
        return {"status": "parse_error", "error_type": type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aemet", action="store_true")
    parser.add_argument("--aemet-history", action="store_true", help="one day, one verified inventory ID")
    parser.add_argument("--meteoclimatic", action="store_true")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(timezone.utc).isoformat()}
    if args.aemet:
        report["aemet_observations"] = aemet_product("observacion/convencional/todas/")
        report["aemet_climatological_inventory"] = aemet_product(
            "valores/climatologicos/inventarioestaciones/todasestaciones/"
        )
    if args.aemet_history:
        day = (datetime.now(timezone.utc) - timedelta(days=10)).date()
        start = day.strftime("%Y-%m-%dT00:00:00UTC")
        end = day.strftime("%Y-%m-%dT23:59:59UTC")
        report["aemet_daily_2462"] = aemet_product(
            f"valores/climatologicos/diarios/datos/fechaini/{start}/fechafin/{end}/estacion/2462"
        )
    if args.meteoclimatic:
        report["meteoclimatic_xml_ES"] = meteoclimatic_feed()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
