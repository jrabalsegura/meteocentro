"""Meteoalerta CAP bundles: synthetic files only, no external network."""

import io
import tarfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from meteocentro.aemet import AemetAdapter
from meteocentro.alerts import days, normalize_warnings
from meteocentro.config import Settings
from meteocentro.forecast import LOCATIONS, store
from meteocentro.job_queue import Queue, db_now
from meteocentro.models import ForecastSnapshot, Job, ProviderRuntime
from meteocentro.worker import run_claim
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_phase1 import client_for

MADRID = LOCATIONS["madrid"]
HUETOR = LOCATIONS["huetor-santillan"]
# Thursday 8 October 2026, 14:00 official time.
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def cap(
    level="amarillo",
    zones=("722802",),
    onset="2026-10-08T16:00:00+02:00",
    expires="2026-10-08T23:59:59+02:00",
    phenomenon="PR;Lluvias",
    kind="Alert",
    identifier="id-1",
    references="",
    sent="2026-10-08T09:00:00-00:00",
    status="Actual",
    parameter="P1;Precipitación acumulada en una hora;15 mm",
    language="es-ES",
    doctype="",
):
    areas = "".join(
        f"<area><areaDesc>Zona {z}</areaDesc><polygon>40,-3 41,-3 40,-3</polygon>"
        f"<geocode><valueName>AEMET-Meteoalerta zona</valueName><value>{z}</value></geocode>"
        "</area>"
        for z in zones
    )
    extra = (
        f"<parameter><valueName>AEMET-Meteoalerta parametro</valueName>"
        f"<value>{parameter}</value></parameter>"
        "<parameter><valueName>AEMET-Meteoalerta probabilidad</valueName>"
        "<value>40%-70%</value></parameter>"
        if level != "verde"
        else ""
    )
    info = f"""<info><language>{language}</language><category>Met</category>
<event>Aviso</event>
<eventCode><valueName>AEMET-Meteoalerta fenomeno</valueName><value>{phenomenon}</value></eventCode>
<effective>2026-10-08T11:00:00+02:00</effective><onset>{onset}</onset><expires>{expires}</expires>
<description>Precipitación acumulada en una hora: 15 mm.</description>
<instruction>Esté atento.</instruction>
<parameter><valueName>AEMET-Meteoalerta nivel</valueName><value>{level}</value></parameter>
{extra}{areas}</info>"""
    english = info.replace(language, "en-GB").replace("Zona", "Zone")
    return f"""<?xml version="1.0" encoding="UTF-8"?>{doctype}
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>{identifier}</identifier><sender>http://www.aemet.es</sender>
<sent>{sent}</sent><status>{status}</status><msgType>{kind}</msgType><scope>Public</scope>
{f"<references>{references}</references>" if references else ""}
{info}{english}</alert>""".encode()


def green(zones=("722801", "722802", "722803", "611801"), day="2026-10-10"):
    return cap(
        level="verde",
        zones=zones,
        onset=f"{day}T00:00:00+02:00",
        expires=f"{day}T23:59:59+02:00",
        identifier=f"green-{day}",
        sent="2026-10-07T21:50:01-00:00",
    )


def bundle(*files, name="Z_CAP.xml"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for index, content in enumerate(files):
            info = tarfile.TarInfo(f"{index}_{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def madrid(*files):
    payload, issued = normalize_warnings(bundle(*files), [MADRID], NOW)["madrid"]
    return payload, issued


def test_no_warning_in_a_complete_snapshot_is_green_for_three_days():
    payload, issued = madrid(green())
    assert payload["warnings"] == [] and payload["covered_until"] == "2026-10-10"
    assert issued == datetime(2026, 10, 7, 21, 50, 1, tzinfo=UTC)
    assert payload["zone_name"] == "Zona 722802"
    assert payload["link"].endswith("avisos?w=hoy&l=722802")
    assert [(d["date"], d["level"]) for d in days(payload, NOW, False)] == [
        ("2026-10-08", "verde"),
        ("2026-10-09", "verde"),
        ("2026-10-10", "verde"),
    ]


def test_days_beyond_the_snapshot_or_a_stale_snapshot_are_unknown_not_green():
    payload, _ = madrid(green(day="2026-10-09"))
    assert [d["level"] for d in days(payload, NOW, False)] == ["verde", "verde", None]
    assert [d["level"] for d in days(payload, NOW, True)] == [None, None, None]


def test_warning_spanning_midnight_counts_on_both_days_with_its_details():
    payload, _ = madrid(
        green(),
        cap(onset="2026-10-08T22:00:00+02:00", expires="2026-10-09T05:59:59+02:00"),
        cap(
            level="naranja",
            phenomenon="AT;Temperaturas máximas",
            identifier="id-2",
            onset="2026-10-08T13:00:00+02:00",
            expires="2026-10-08T20:59:59+02:00",
            parameter="TA;Temperatura máxima;39 ºC",
        ),
    )
    today, tomorrow, _ = days(payload, NOW, False)
    assert today["level"] == "naranja" and tomorrow["level"] == "amarillo"
    assert [w["phenomenon"] for w in today["warnings"]] == [
        "Temperaturas máximas",
        "Lluvias",
    ]
    rain = tomorrow["warnings"][0]
    assert rain["threshold"] == "Precipitación acumulada en una hora: 15 mm"
    assert rain["probability"] == "40%-70%" and rain["phenomenon_code"] == "PR"
    assert rain["onset"] == "2026-10-08T20:00:00+00:00"
    # Tomorrow at 03:00 the rain is still on; from 06:00 that day has no warning left.
    night = datetime(2026, 10, 9, 1, tzinfo=UTC)
    morning = datetime(2026, 10, 9, 4, tzinfo=UTC)
    assert days(payload, night, False)[0]["level"] == "amarillo"
    assert days(payload, morning, False)[0] == {
        "date": "2026-10-09",
        "level": "verde",
        "warnings": [],
    }


def test_expired_cancelled_superseded_and_other_zones_are_ignored():
    payload, _ = madrid(
        green(),
        cap(identifier="expired", expires="2026-10-08T05:59:59+02:00"),
        cap(identifier="cancelled", kind="Cancel"),
        cap(identifier="old"),
        cap(
            identifier="new",
            kind="Update",
            level="naranja",
            references="http://www.aemet.es,old,2026-10-08T09:00:00-00:00",
            sent="2026-10-08T10:00:00-00:00",
        ),
        cap(identifier="elsewhere", zones=("722801",), level="rojo"),
        cap(identifier="exercise", status="Exercise", level="rojo"),
    )
    assert [w["level"] for w in payload["warnings"]] == ["naranja"]


def test_one_bundle_serves_every_place_of_its_community():
    payloads = normalize_warnings(
        bundle(green(), cap(zones=("611801",))), [MADRID, HUETOR], NOW
    )
    assert payloads["madrid"][0]["warnings"] == []
    assert len(payloads["huetor-santillan"][0]["warnings"]) == 1


@pytest.mark.parametrize(
    "files",
    [
        (),
        (green(zones=("722801",)),),
        (cap(level="morado"),),
        (cap(phenomenon="Lluvias"),),
        (cap(expires="2026-10-08T23:59:59"),),
        (cap(language="en-GB"),),
        (b"<html>no</html>",),
        (b"<alert",),
    ],
)
def test_contract_changes_are_rejected(files):
    with pytest.raises(ValueError):
        normalize_warnings(bundle(*files), [MADRID], NOW)


def test_not_an_archive_is_rejected():
    with pytest.raises(ValueError):
        normalize_warnings(b'{"estado": 200}', [MADRID], NOW)


def test_dtd_is_refused():
    from meteocentro.ingestion_errors import IngestionError

    hostile = green(zones=("722802",)).replace(
        b"?>", b'?><!DOCTYPE alert [<!ENTITY x "x">]>', 1
    )
    with pytest.raises(IngestionError):
        normalize_warnings(bundle(hostile), [MADRID], NOW)


# --- Worker job and API ------------------------------------------------------------------


def warnings_transport(calls, *, broken=False):
    def handler(request):
        calls.append(request)
        path = request.url.path
        if "/api/avisos_cap/ultimoelaborado/area/" in path:
            assert request.headers["api_key"] == "synthetic-key"
            area = path.rstrip("/").rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "estado": 200,
                    "datos": f"https://opendata.aemet.es/opendata/sh/avisos-{area}",
                    "metadatos": "https://opendata.aemet.es/opendata/sh/meta",
                },
            )
        assert "api_key" not in request.headers
        area = path.rsplit("-", 1)[-1]
        if broken:
            return httpx.Response(200, content=b"not a tar")
        zones = ("722801", "722802") if area == "72" else ("611801",)
        future = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
        return httpx.Response(200, content=bundle(green(zones=zones, day=future)))

    return httpx.MockTransport(handler)


@pytest.fixture
def warnings_queue(db, engine):
    settings = Settings(
        database_url=str(engine.url), aemet_api_key="synthetic-key", _env_file=None
    )
    queue = Queue(engine, settings)
    queue.schedule()
    with Session(engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.kind == "warnings")
            .values(next_run_at=db_now(session) - timedelta(seconds=1))
        )
    return queue


def run_warnings(queue, transport):
    claim = queue.claim("warnings")
    assert claim

    def factory(key, **kwargs):
        return AemetAdapter(key, transport=transport, **kwargs)

    return run_claim(queue, claim, adapter_factory=factory), claim


def test_warnings_job_uses_the_shared_quota_once_per_community(warnings_queue, db):
    calls = []
    report, claim = run_warnings(warnings_queue, warnings_transport(calls))
    assert report["status"] == "succeeded" and report["result"]["warnings_saved"] == 2
    assert len(calls) == 4 and db.scalar(select(ProviderRuntime.day_calls)) == 4
    assert sorted(c.url.path.rsplit("/", 1)[-1] for c in calls[::2]) == ["61", "72"]
    rows = db.scalars(select(ForecastSnapshot)).all()
    assert {(r.source, r.location, r.product) for r in rows} == {
        ("aemet", "madrid", "warnings"),
        ("aemet", "huetor-santillan", "warnings"),
    }
    job = db.get(Job, claim.job_id)
    assert job.status == "pending" and job.interval_seconds == 7200
    assert job.priority < db.scalar(select(Job.priority).where(Job.kind == "current"))


def test_warnings_contract_change_retries_without_pausing_observations(
    warnings_queue, db
):
    report, _ = run_warnings(warnings_queue, warnings_transport([], broken=True))
    assert report == {"status": "retry", "code": "warnings_contract_changed"}
    assert db.scalar(select(ProviderRuntime.pause_reason)) is None
    assert warnings_queue.claim("current")
    assert db.scalar(select(func.count()).select_from(ForecastSnapshot)) == 0


def test_warnings_disabled_is_not_scheduled(db, engine):
    settings = Settings(
        database_url=str(engine.url),
        aemet_api_key="synthetic-key",
        aemet_warnings_enabled=False,
        _env_file=None,
    )
    Queue(engine, settings).schedule()
    assert (
        db.scalar(select(func.count()).select_from(Job).where(Job.kind == "warnings"))
        == 0
    )


def test_forecast_api_returns_warning_days_and_marks_old_snapshots_unknown(db):
    now = datetime.now(UTC)
    future = (now + timedelta(days=3)).date().isoformat()
    for location, fetched in ((MADRID, now), (HUETOR, now - timedelta(hours=7))):
        payload, issued = normalize_warnings(
            bundle(green(day=future)), [location], now
        )[location.code]
        store(db, "aemet", location.code, "warnings", payload, issued, fetched)
    db.commit()
    with client_for(db) as client:
        body = client.get("/api/v1/forecasts").json()
    madrid_place, huetor_place = body["locations"]
    fresh = madrid_place["warnings"]
    assert fresh["stale"] is False and fresh["data"]["zone"] == "722802"
    assert [d["level"] for d in fresh["data"]["days"]] == ["verde"] * 3
    old = huetor_place["warnings"]
    assert old["stale"] is True
    assert [d["level"] for d in old["data"]["days"]] == [None] * 3
