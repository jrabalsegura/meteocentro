import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from meteocentro.aemet import AemetAdapter
from meteocentro.config import Settings
from meteocentro.forecast import (
    DETERMINISTIC,
    ENSEMBLES,
    LOCATIONS,
    OPEN_METEO_JOB,
    normalize_aemet_daily,
    normalize_aemet_hourly,
    normalize_deterministic,
    normalize_ensemble,
    refresh_open_meteo,
    store,
)
from meteocentro.ingestion_errors import IngestionError
from meteocentro.job_queue import Queue, db_now
from meteocentro.models import ForecastSnapshot, Job, ProviderRuntime
from meteocentro.worker import run_claim
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_phase1 import client_for

MADRID = LOCATIONS["madrid"]
HUETOR = LOCATIONS["huetor-santillan"]
T0 = int(datetime(2026, 9, 27, tzinfo=UTC).timestamp())


def daily(municipality="28079", **day):
    return [
        {
            "elaborado": "2026-09-27T12:57:07",
            "nombre": "Sintético",
            "id": int(municipality),
            "prediccion": {
                "dia": [
                    {
                        "probPrecipitacion": [
                            {"value": 0, "periodo": "00-24"},
                            {"value": 40, "periodo": "12-24"},
                        ],
                        "cotaNieveProv": [{"value": "", "periodo": "00-24"}],
                        "estadoCielo": [
                            {"value": "", "periodo": "00-24", "descripcion": ""},
                            {
                                "value": "43n",
                                "periodo": "12-24",
                                "descripcion": "Intervalos nubosos con lluvia escasa",
                            },
                        ],
                        "viento": [
                            {"direccion": "NE", "velocidad": 15, "periodo": "00-24"}
                        ],
                        "rachaMax": [{"value": "", "periodo": "00-24"}],
                        "temperatura": {
                            "maxima": 30,
                            "minima": 0,
                            "dato": [{"value": 20, "hora": 6}],
                        },
                        "sensTermica": {"maxima": 31, "minima": -1},
                        "humedadRelativa": {"maxima": 150, "minima": 20},
                        "uvMax": 5,
                        "fecha": "2026-09-27T00:00:00",
                        **day,
                    },
                    {
                        "probPrecipitacion": [{"value": 95}],
                        "estadoCielo": [
                            {"value": "25", "descripcion": "Muy nuboso con lluvia"}
                        ],
                        "temperatura": {"maxima": 22, "minima": 15, "dato": []},
                        "fecha": "2026-09-28T00:00:00",
                    },
                ]
            },
        }
    ]


def hourly_day(date, hours, **extra):
    return {
        "fecha": f"{date}T00:00:00",
        "orto": "08:06",
        "ocaso": "20:04",
        "estadoCielo": [
            {"value": "11n", "periodo": h, "descripcion": "Despejado"} for h in hours
        ],
        "temperatura": [
            {"value": str(10 + i), "periodo": h} for i, h in enumerate(hours)
        ],
        "precipitacion": [
            {"value": "Ip" if h == "01" else "0", "periodo": h} for h in hours
        ],
        "nieve": [{"value": "", "periodo": h} for h in hours],
        "vientoAndRachaMax": [
            item
            for h in hours
            for item in (
                {"direccion": ["NE"], "velocidad": ["12"], "periodo": h},
                {"value": "19", "periodo": h},
            )
        ],
        "probPrecipitacion": [{"value": "30", "periodo": "2002"}],
        **extra,
    }


def hourly(days):
    return [
        {
            "elaborado": "2026-10-24T20:00:00",
            "id": "28079",
            "nombre": "Sintético",
            "prediccion": {"dia": days},
        }
    ]


def test_aemet_daily_keeps_missing_values_apart_from_zero():
    payload, issued = normalize_aemet_daily(daily(), MADRID)
    # Official (local) time: 12:57 CEST is 10:57 UTC.
    assert issued == datetime(2026, 9, 27, 10, 57, 7, tzinfo=UTC)
    first, second = payload["days"]
    whole = first["periods"][0]
    assert whole["period"] == "00-24" and whole["sky_code"] is None
    assert whole["precipitation_probability"] == 0 and whole["snow_level"] is None
    assert whole["gust"] is None and whole["wind_speed"] == 15
    afternoon = first["periods"][1]
    assert (
        afternoon["sky_code"] == "43" and afternoon["precipitation_probability"] == 40
    )
    assert first["temperature"]["min"] == 0 and first["feels_like"]["min"] == -1
    # Outside the physical range: not published, never clipped.
    assert first["humidity"] == {"max": None, "min": 20}
    assert second["periods"] == [
        {
            "period": "00-24",
            "sky_code": "25",
            "sky": "Muy nuboso con lluvia",
            "precipitation_probability": 95,
        }
    ]
    assert second["uv_max"] is None and second["humidity"] == {"max": None, "min": None}


@pytest.mark.parametrize(
    "change",
    [
        {"fecha": "27/09/2026"},
        {"estadoCielo": [{"value": "nube", "periodo": "00-24"}]},
        {"probPrecipitacion": [{"value": "mucho", "periodo": "00-24"}]},
        {"viento": [{"direccion": "N", "velocidad": 5, "periodo": "mañana"}]},
    ],
)
def test_aemet_daily_rejects_contract_changes(change):
    with pytest.raises(ValueError):
        normalize_aemet_daily(daily(**change), MADRID)


def test_aemet_forecast_for_another_municipality_is_rejected():
    with pytest.raises(ValueError, match="wrong_municipality"):
        normalize_aemet_daily(daily("18099"), MADRID)


def test_aemet_hourly_trace_rain_and_autumn_clock_change():
    # 25-10-2026: 02:00-02:59 occurs twice (CEST, then CET).
    payload, _ = normalize_aemet_hourly(
        hourly([hourly_day("2026-10-25", ["00", "01", "02", "02", "03"])]), MADRID
    )
    times = [h["time"] for h in payload["hours"]]
    assert times == [
        "2026-10-24T22:00:00+00:00",
        "2026-10-24T23:00:00+00:00",
        "2026-10-25T00:00:00+00:00",
        "2026-10-25T01:00:00+00:00",
        "2026-10-25T02:00:00+00:00",
    ]
    assert [h["temperature"] for h in payload["hours"]] == [10, 11, 12, 13, 14]
    trace = payload["hours"][1]
    assert trace["precipitation"] is None and trace["precipitation_trace"] is True
    assert payload["hours"][0]["precipitation"] == 0
    assert payload["hours"][0]["precipitation_trace"] is False
    assert payload["hours"][0]["snow"] is None and payload["hours"][0]["night"] is True
    assert payload["hours"][0]["wind_speed"] == 12 and payload["hours"][0]["gust"] == 19
    # "2002" crosses midnight, and the next night is one hour longer in CET.
    assert payload["windows"] == [
        {
            "start": "2026-10-25T19:00:00+00:00",
            "end": "2026-10-26T01:00:00+00:00",
            "precipitation": 30,
        }
    ]
    assert payload["link"].endswith("/municipios/horas/madrid-id28079")


def test_aemet_hourly_omits_the_hour_skipped_in_spring():
    payload, _ = normalize_aemet_hourly(
        hourly([hourly_day("2027-03-28", ["01", "02", "03"])]), MADRID
    )
    assert [h["time"] for h in payload["hours"]] == [
        "2027-03-28T00:00:00+00:00",
        "2027-03-28T01:00:00+00:00",
    ]


def model_document(models, hours=48, **overrides):
    """Open-Meteo shape: one hourly axis, suffixed series, unix time, UTC."""
    times = [T0 + 3600 * i for i in range(hours)]
    hourly, units = {"time": times}, {"time": "unixtime"}
    for model in models:
        for variable, unit in (
            ("temperature_2m", "°C"),
            ("dew_point_2m", "°C"),
            ("temperature_850hPa", "°C"),
            ("pressure_msl", "hPa"),
            ("wind_speed_10m", "km/h"),
            ("wind_gusts_10m", "km/h"),
            ("wind_direction_10m", "°"),
            ("cloud_cover_low", "%"),
            ("cloud_cover_mid", "%"),
            ("cloud_cover_high", "%"),
            ("precipitation", "mm"),
            ("snowfall", "cm"),
        ):
            key = f"{variable}_{model.suffix}"
            hourly[key] = [float(i % 7) for i in range(hours)]
            units[key] = unit
    hourly.update(overrides)
    return {
        "latitude": 40.5,
        "longitude": -3.75,
        "elevation": 666.0,
        "hourly": hourly,
        "hourly_units": units,
    }


def ensemble_document(members=5, hours=24):
    times = [T0 + 3600 * i for i in range(hours)]
    hourly, units = {"time": times}, {"time": "unixtime"}
    for model in ENSEMBLES:
        for variable, unit in (("temperature_850hPa", "°C"), ("precipitation", "mm")):
            for member in range(members + 1):
                name = "" if member == 0 else f"member{member:02d}_"
                key = f"{variable}_{name}{model.suffix}"
                hourly[key] = [float(member) for _ in times]
                units[key] = unit
    return {"hourly": hourly, "hourly_units": units}


def test_deterministic_precipitation_is_summed_in_complete_six_hour_windows():
    gfs = DETERMINISTIC[0]
    rain = [0.0] * 48
    rain[1:7] = [0.9, 0.9, 0.9, 0.4, 0.4, 0.4]  # ECMWF-like 3 h totals spread per hour.
    rain[10] = None
    document = model_document([gfs], precipitation_gfs_global=rain)
    run = datetime(2026, 9, 27, 6, tzinfo=UTC)
    payload = normalize_deterministic(document, gfs, run)
    six_hours = payload["accumulated_6h"]
    # The 00 UTC window starts before the returned range and is not invented.
    assert six_hours["time"][:3] == [T0 + 6 * 3600, T0 + 12 * 3600, T0 + 18 * 3600]
    assert six_hours["precipitation"][:3] == [3.9, None, 0.0]
    assert payload["time"][:2] == [T0, T0 + 3 * 3600]
    assert payload["series"]["temperature_2m"][:2] == [0.0, 3.0]
    assert payload["run"] == "2026-09-27T06:00:00+00:00"
    assert payload["grid"] == {"latitude": 40.5, "longitude": -3.75, "elevation": 666.0}


def test_deterministic_horizon_is_trimmed_per_model_without_filling():
    gfs, ecmwf = DETERMINISTIC
    document = model_document([gfs, ecmwf])
    for key in list(document["hourly"]):
        if key.endswith("_ecmwf_ifs025"):
            document["hourly"][key] = document["hourly"][key][:24] + [None] * 24
    assert normalize_deterministic(document, gfs, None)["time"][-1] == T0 + 45 * 3600
    ecmwf_payload = normalize_deterministic(document, ecmwf, None)
    assert ecmwf_payload["time"][-1] == T0 + 21 * 3600
    assert ecmwf_payload["accumulated_6h"]["time"][-1] == T0 + 18 * 3600


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["hourly_units"].update({"temperature_2m_gfs_global": "°F"}),
        lambda d: d["hourly"].pop("pressure_msl_gfs_global"),
        lambda d: d["hourly"]["time"].__setitem__(3, T0),
        lambda d: d["hourly"]["wind_speed_10m_gfs_global"].__setitem__(0, "5"),
    ],
)
def test_deterministic_rejects_contract_changes(change):
    document = model_document([DETERMINISTIC[0]])
    change(document)
    with pytest.raises(ValueError):
        normalize_deterministic(document, DETERMINISTIC[0], None)


def test_ensemble_mean_requires_every_member():
    document = ensemble_document()
    gefs = ENSEMBLES[0]
    document["hourly"]["temperature_850hPa_member03_ncep_gefs05"] = None
    with pytest.raises(ValueError):
        normalize_ensemble(document, gefs, None)
    document = ensemble_document(members=10)
    key = "temperature_850hPa_member03_ncep_gefs05"
    document["hourly"][key][6] = None
    payload = normalize_ensemble(document, gefs, None)
    assert payload["members"] == 11
    t850 = payload["temperature_850hPa"]
    assert t850["time"] == [T0, T0 + 6 * 3600, T0 + 12 * 3600, T0 + 18 * 3600]
    assert t850["mean"] == [5.0, None, 5.0, 5.0]
    assert t850["members"][3][1] is None and t850["members"][0][0] == 0
    rain = payload["precipitation_6h"]
    assert rain["time"] == [T0 + 6 * 3600, T0 + 12 * 3600, T0 + 18 * 3600]
    assert rain["members"][2] == [12.0, 12.0, 12.0] and rain["mean"][0] == 30.0


def test_ensemble_with_too_few_members_is_rejected():
    with pytest.raises(ValueError, match="missing_members"):
        normalize_ensemble(ensemble_document(members=3), ENSEMBLES[1], None)


def test_older_run_never_replaces_a_newer_snapshot(db):
    newer = datetime(2026, 9, 27, 6, tzinfo=UTC)
    now = datetime.now(UTC)
    assert store(db, "open_meteo", "madrid", "deterministic_gfs", {"v": 2}, newer, now)
    assert not store(
        db,
        "open_meteo",
        "madrid",
        "deterministic_gfs",
        {"v": 1},
        newer - timedelta(hours=6),
        now,
    )
    assert store(db, "open_meteo", "madrid", "deterministic_gfs", {"v": 3}, newer, now)
    db.commit()
    assert db.scalar(select(ForecastSnapshot.payload)) == {"v": 3}


def aemet_forecast_transport(calls, *, broken=False, status=200):
    def handler(request):
        calls.append(request)
        path = request.url.path
        if "/api/prediccion/especifica/municipio/" in path:
            assert request.headers["api_key"] == "synthetic-key"
            if status != 200:
                return httpx.Response(status)
            kind, municipality = path.rstrip("/").split("/")[-2:]
            return httpx.Response(
                200,
                json={
                    "estado": 200,
                    "datos": f"https://opendata.aemet.es/opendata/sh/{kind}-{municipality}",
                    "metadatos": "https://opendata.aemet.es/opendata/sh/meta",
                },
            )
        assert "api_key" not in request.headers
        kind, municipality = path.rsplit("/", 1)[-1].split("-")
        if broken:
            return httpx.Response(200, json=[{"id": municipality}])
        body = (
            daily(municipality)
            if kind == "diaria"
            else [
                {
                    **hourly([hourly_day("2026-09-27", ["20", "21"])])[0],
                    "id": municipality,
                }
            ]
        )
        # AEMET serves these files as ISO-8859-15 text.
        return httpx.Response(
            200, content=json.dumps(body, ensure_ascii=False).encode("latin-1")
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def aemet_queue(db, engine):
    settings = Settings(
        database_url=str(engine.url), aemet_api_key="synthetic-key", _env_file=None
    )
    queue = Queue(engine, settings)
    queue.schedule()
    with Session(engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.kind == "forecast")
            .values(next_run_at=db_now(session) - timedelta(seconds=1))
        )
    return queue


def run_forecast(queue, transport):
    claim = queue.claim("forecast")
    assert claim
    factory = lambda key, **kwargs: AemetAdapter(key, transport=transport, **kwargs)
    return run_claim(queue, claim, adapter_factory=factory), claim


def test_aemet_forecast_job_uses_the_shared_quota_and_stores_both_places(
    aemet_queue, db
):
    calls = []
    report, claim = run_forecast(aemet_queue, aemet_forecast_transport(calls))
    assert report["status"] == "succeeded" and report["result"]["forecasts_saved"] == 4
    assert len(calls) == 8
    assert db.scalar(select(ProviderRuntime.day_calls)) == 8
    rows = db.scalars(
        select(ForecastSnapshot).order_by(ForecastSnapshot.location)
    ).all()
    assert {(r.source, r.location, r.product) for r in rows} == {
        ("aemet", code, product)
        for code in ("madrid", "huetor-santillan")
        for product in ("daily", "hourly")
    }
    job = db.get(Job, claim.job_id)
    assert job.status == "pending" and job.interval_seconds == 10800
    assert job.priority < db.scalar(select(Job.priority).where(Job.kind == "current"))


def test_aemet_forecast_contract_change_retries_without_pausing_observations(
    aemet_queue, db
):
    report, claim = run_forecast(aemet_queue, aemet_forecast_transport([], broken=True))
    assert report == {"status": "retry", "code": "forecast_contract_changed"}
    assert db.scalar(select(ProviderRuntime.pause_reason)) is None
    assert db.get(Job, claim.job_id).status == "retry"
    assert aemet_queue.claim("current")
    assert db.scalar(select(func.count()).select_from(ForecastSnapshot)) == 0


def test_aemet_forecast_disabled_is_not_scheduled(db, engine):
    settings = Settings(
        database_url=str(engine.url),
        aemet_api_key="synthetic-key",
        aemet_forecast_enabled=False,
        _env_file=None,
    )
    Queue(engine, settings).schedule()
    assert (
        db.scalar(select(func.count()).select_from(Job).where(Job.kind == "forecast"))
        == 0
    )


def open_meteo_transport(calls, *, fail_ensemble=False):
    def handler(request):
        calls.append(request)
        assert "apikey" not in str(request.url)
        if request.url.path.endswith("/meta.json"):
            return httpx.Response(
                200, json={"last_run_initialisation_time": T0 + 6 * 3600}
            )
        if request.url.host == "ensemble-api.open-meteo.com":
            if fail_ensemble:
                return httpx.Response(502)
            return httpx.Response(200, json=ensemble_document(members=10))
        assert request.url.params["timezone"] == "UTC"
        return httpx.Response(200, json=model_document(DETERMINISTIC))

    return httpx.MockTransport(handler)


def open_meteo_settings(engine, **values):
    return Settings(database_url=str(engine.url), _env_file=None, **values)


def test_open_meteo_refresh_is_scheduled_and_never_repeats_early(db, engine):
    calls = []
    settings = open_meteo_settings(engine)
    report = refresh_open_meteo(engine, settings, transport=open_meteo_transport(calls))
    assert report["status"] == "succeeded" and len(report["saved"]) == 6
    # 4 metadata files, 2 deterministic points, 1 ensemble point (Madrid only).
    assert len(calls) == 7
    assert (
        refresh_open_meteo(engine, settings, transport=open_meteo_transport(calls))
        is None
    )
    assert len(calls) == 7
    job = db.scalar(select(Job).where(Job.dedupe_key == OPEN_METEO_JOB))
    assert job.next_run_at - db_now(db) > timedelta(minutes=55)
    ensemble = db.scalar(
        select(ForecastSnapshot).where(ForecastSnapshot.product == "ensemble_ecmwf")
    )
    assert ensemble.location == "madrid"
    assert ensemble.issued_at == datetime.fromtimestamp(T0 + 6 * 3600, UTC)
    assert not db.scalar(
        select(func.count())
        .select_from(ForecastSnapshot)
        .where(
            ForecastSnapshot.location == "huetor-santillan",
            ForecastSnapshot.product.like("ensemble%"),
        )
    )


def test_open_meteo_failure_keeps_previous_snapshot_and_retries_sooner(db, engine):
    settings = open_meteo_settings(engine)
    refresh_open_meteo(engine, settings, transport=open_meteo_transport([]))
    before = db.scalar(
        select(ForecastSnapshot.fetched_at).where(
            ForecastSnapshot.product == "ensemble_gfs"
        )
    )
    report = refresh_open_meteo(
        engine,
        settings,
        transport=open_meteo_transport([], fail_ensemble=True),
        force=True,
    )
    assert report["status"] == "partial"
    assert report["errors"] == {
        "madrid/ensemble_gfs": "provider_transient",
        "madrid/ensemble_ecmwf": "provider_transient",
    }
    db.expire_all()
    assert (
        db.scalar(
            select(ForecastSnapshot.fetched_at).where(
                ForecastSnapshot.product == "ensemble_gfs"
            )
        )
        == before
    )
    job = db.scalar(select(Job).where(Job.dedupe_key == OPEN_METEO_JOB))
    assert job.next_run_at - db_now(db) <= timedelta(minutes=10)
    assert job.cursor["errors"]["madrid/ensemble_gfs"] == "provider_transient"


def test_open_meteo_disabled_makes_no_requests(db, engine):
    calls = []
    settings = open_meteo_settings(engine, open_meteo_enabled=False)
    assert (
        refresh_open_meteo(engine, settings, transport=open_meteo_transport(calls))
        is None
    )
    assert calls == []


def test_open_meteo_client_refuses_other_hosts():
    from meteocentro.forecast import OpenMeteo

    client = OpenMeteo(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    with pytest.raises(IngestionError, match="unsafe_data_url"):
        client.get("https://example.invalid/v1/forecast")
    client.close()


def test_forecast_api_returns_snapshots_with_staleness(db):
    now = datetime.now(UTC)
    store(db, "aemet", "madrid", "daily", {"days": []}, now, now - timedelta(hours=10))
    store(db, "open_meteo", "madrid", "ensemble_gfs", {"members": 31}, None, now)
    store(
        db,
        "open_meteo",
        "huetor-santillan",
        "deterministic_ecmwf",
        {"time": []},
        None,
        now,
    )
    db.commit()
    with client_for(db) as client:
        response = client.get("/api/v1/forecasts")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    madrid, huetor = body["locations"]
    assert madrid["code"] == "madrid" and huetor["name"] == "Huétor de Santillán"
    assert madrid["aemet"]["daily"]["stale"] is True
    assert madrid["aemet"]["hourly"] is None
    assert madrid["ensembles"]["gfs"]["data"] == {"members": 31}
    assert madrid["ensembles"]["gfs"]["stale"] is False
    assert huetor["ensembles"] is None
    assert huetor["models"]["ecmwf"]["data"] == {"time": []}
    assert {item["source"] for item in body["attribution"]} == {"aemet", "open_meteo"}
