"""Explicit bounded diagnostics. No DB writes, raw weather records or secrets in output."""

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime

from meteocentro.aemet import AemetAdapter, station_record
from meteocentro.domain.provinces import classify_province
from meteocentro.ingestion_errors import IngestionError
from meteocentro.meteoclimatic import PREFIXES, MeteoclimaticAdapter, normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meteoclimatic", action="store_true")
    parser.add_argument("--aemet", action="store_true")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(UTC).isoformat()}
    calls = 0

    def reserve():
        nonlocal calls
        if calls >= 7:
            raise IngestionError("diagnostic_budget")
        calls += 1

    if args.meteoclimatic:
        adapter = MeteoclimaticAdapter(reserve=reserve)
        try:
            data = adapter.probe()
            normalized, invalid = 0, 0
            for row in data.records:
                try:
                    normalize(row, data)
                    normalized += 1
                except (ValueError, TypeError, KeyError, OverflowError):
                    invalid += 1
            report["meteoclimatic"] = {
                "technical_access": "verified",
                "http_status": adapter.last_http_status,
                "operational_status": "pending_terms",
                "received": len(data.records),
                "normalizable_current": normalized,
                "invalid_current": invalid,
                "prefix_counts": {
                    prefix: sum(row.get("id", "").startswith(prefix) for row in data.records)
                    for prefix in PREFIXES
                },
                "coordinate_fields": sorted(
                    {
                        field
                        for row in data.records
                        for field in row
                        if field in {"lat", "lon", "latitude", "longitude"}
                    }
                ),
                "stored_observations": 0,
            }
        except IngestionError as error:
            report["meteoclimatic"] = {
                "technical_access": "error",
                "http_status": adapter.last_http_status,
                "code": error.code,
                "operational_status": "pending_terms",
            }
        finally:
            adapter.close()
    if args.aemet:
        adapter = AemetAdapter(os.getenv("AEMET_API_KEY"), reserve=reserve)
        try:
            for product in ("current", "inventory"):
                data = adapter.download(product)
                provinces, invalid = Counter(), 0
                for row in data.records:
                    try:
                        info = station_record(row, product)
                        code = classify_province(float(info["longitude"]), float(info["latitude"]))
                        provinces[code or "outside"] += 1
                    except (ValueError, TypeError, KeyError, OverflowError):
                        invalid += 1
                report["aemet_" + product] = {
                    "technical_access": "verified",
                    "records": len(data.records),
                    "provinces": provinces,
                    "invalid": invalid,
                    "stored_observations": 0,
                }
        except IngestionError as error:
            report["aemet"] = {"code": error.code}
        finally:
            adapter.close()
    report["http_attempts"] = calls
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
