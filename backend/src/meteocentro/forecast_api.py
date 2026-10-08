"""Read-only forecasts from the worker's snapshots; the browser never calls providers."""

import json
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from meteocentro.alerts import days as warning_days
from meteocentro.db import get_session
from meteocentro.forecast import DETERMINISTIC, ENSEMBLES, LOCATIONS
from meteocentro.models import ForecastSnapshot

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_session)]
# About three missed refreshes of each job (AEMET every 3 h, Open-Meteo every hour).
STALE_AFTER = {"aemet": timedelta(hours=9), "open_meteo": timedelta(hours=4)}
# Warnings change within hours; past this age the three days are shown as unknown.
WARNINGS_STALE_AFTER = timedelta(hours=6)
ATTRIBUTION = [
    {
        "source": "aemet",
        "text": "© AEMET. Predicción municipal y avisos Meteoalerta elaborados por la Agencia "
        "Estatal de Meteorología.",
        "url": "https://www.aemet.es/es/nota_legal",
    },
    {
        "source": "ncep",
        "text": "Media climática 1991-2020 a 850 hPa: NCEP/NCAR Reanalysis 1 (NOAA PSL).",
        "url": "https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html",
    },
    {
        "source": "open_meteo",
        "text": "Datos de modelos GFS (NOAA) y ECMWF servidos por Open-Meteo.com (CC BY 4.0).",
        "url": "https://open-meteo.com/",
    },
]


@cache
def climatology():
    """Static 850 hPa reference (scripts/fetch_t850_climatology.py), never mixed with forecasts."""
    document = json.loads(
        (Path(__file__).parent / "reference/t850_climatology.json").read_text("utf-8")
    )
    return {
        code: {
            "period": document["period"],
            "dataset": document["dataset"],
            "values": place["values"],
        }
        for code, place in document["places"].items()
    }


@router.get("/forecasts")
def forecasts(db: Db):
    now = db.scalar(select(func.now()))
    snapshots = {(s.source, s.location, s.product): s for s in db.scalars(select(ForecastSnapshot))}

    def warnings(location):
        item = snapshots.get(("aemet", location, "warnings"))
        if item is None:
            return None
        stale = now - item.fetched_at > WARNINGS_STALE_AFTER
        return {
            "issued_at": item.issued_at,
            "fetched_at": item.fetched_at,
            "stale": stale,
            "data": {
                "zone": item.payload["zone"],
                "zone_name": item.payload["zone_name"],
                "link": item.payload["link"],
                "days": warning_days(item.payload, now, stale),
            },
        }

    def snapshot(source, location, product):
        item = snapshots.get((source, location, product))
        if item is None:
            return None
        return {
            "issued_at": item.issued_at,
            "fetched_at": item.fetched_at,
            "stale": now - item.fetched_at > STALE_AFTER[source],
            "data": item.payload,
        }

    return {
        "locations": [
            {
                "code": location.code,
                "name": location.name,
                "latitude": location.latitude,
                "longitude": location.longitude,
                "aemet_url": location.aemet_url,
                "aemet": {
                    product: snapshot("aemet", location.code, product)
                    for product in ("daily", "hourly")
                },
                "models": {
                    model.code: snapshot("open_meteo", location.code, f"deterministic_{model.code}")
                    for model in DETERMINISTIC
                },
                "ensembles": {
                    model.code: snapshot("open_meteo", location.code, f"ensemble_{model.code}")
                    for model in ENSEMBLES
                }
                if location.ensemble
                else None,
                "warnings": warnings(location.code),
                "climate_850hPa": climatology().get(location.code),
            }
            for location in LOCATIONS.values()
        ],
        "attribution": ATTRIBUTION,
    }
