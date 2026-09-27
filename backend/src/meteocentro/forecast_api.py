"""Read-only forecasts from the worker's snapshots; the browser never calls providers."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from meteocentro.db import get_session
from meteocentro.forecast import DETERMINISTIC, ENSEMBLES, LOCATIONS
from meteocentro.models import ForecastSnapshot

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_session)]
# About three missed refreshes of each job (AEMET every 3 h, Open-Meteo every hour).
STALE_AFTER = {"aemet": timedelta(hours=9), "open_meteo": timedelta(hours=4)}
ATTRIBUTION = [
    {
        "source": "aemet",
        "text": "© AEMET. Predicción municipal elaborada por la Agencia Estatal de Meteorología.",
        "url": "https://www.aemet.es/es/nota_legal",
    },
    {
        "source": "open_meteo",
        "text": "Datos de modelos GFS (NOAA) y ECMWF servidos por Open-Meteo.com (CC BY 4.0).",
        "url": "https://open-meteo.com/",
    },
]


@router.get("/forecasts")
def forecasts(db: Db):
    now = db.scalar(select(func.now()))
    snapshots = {(s.source, s.location, s.product): s for s in db.scalars(select(ForecastSnapshot))}

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
            }
            for location in LOCATIONS.values()
        ],
        "attribution": ATTRIBUTION,
    }
