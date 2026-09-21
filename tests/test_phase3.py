import copy
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from meteocentro.aemet import Batch
from meteocentro.catalog import (
    link_source,
    recheck_boundary_locations,
    recheck_duplicate_radius,
)
from meteocentro.catalog_cli import register_manual
from meteocentro.config import Settings
from meteocentro.ingestion_errors import IngestionError, LeaseLost
from meteocentro.job_queue import Queue, db_now
from meteocentro.meteoclimatic import (
    MeteoclimaticAdapter,
    MeteoclimaticIngestor,
    normalize,
    parse_xml,
    quality,
)
from meteocentro.models import (
    AuditEvent,
    DuplicateCandidate,
    Exclusion,
    IdentityExclusion,
    Job,
    Observation,
    Provider,
    ProviderRuntime,
    Station,
    StationLocationHistory,
    StationSource,
)
from meteocentro.worker import run_claim, status
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
from test_phase1 import client_for
from test_phase2 import ROW, batch, commit_batch, count

ID = "ESMAD2800000000000A"
XML = (
    (Path(__file__).parent / "fixtures/meteoclimatic_sintetico.xml")
    .read_bytes()
    .replace(b"ESXXX0000000000000A", ID.encode())
)


@pytest.fixture
def mc(db, engine):
    settings = Settings(
        database_url=str(engine.url),
        _env_file=None,
        aemet_api_key="synthetic",
        meteoclimatic_enabled=True,
        meteoclimatic_terms_reference="fixture-only-permission",
    )
    queue = Queue(engine, settings, "meteoclimatic")
    queue.schedule()
    return queue


def provider(db, code="meteoclimatic"):
    return db.scalar(select(Provider).where(Provider.code == code))


def manual(db, external_id=ID, lat="40.4168", lon="-3.7038", precision="exact"):
    result = register_manual(
        db,
        provider(db),
        external_id,
        name="Estación sintética",
        latitude=lat,
        longitude=lon,
        precision=precision,
        evidence="synthetic fixture location" if lat is not None else None,
    )
    db.commit()
    return result


def mc_commit(queue, data=None):
    with Session(queue.engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.dedupe_key == "meteoclimatic:current")
            .values(next_run_at=db_now(session), status="pending")
        )
    claim = queue.claim("current")
    assert claim
    result, cursor = MeteoclimaticIngestor(queue, claim).ingest(data or parse_xml(XML))
    queue.succeed(claim, result, cursor)
    return result


def test_xml_encoding_station_clock_null_zero_units_quality_and_unresolved_periods():
    data = parse_xml(
        XML.replace(b"Lugar inventado", "Ávila".encode()).replace(
            b"<rain>",
            b"<humidity><unit>%</unit><now/></humidity>"
            b"<wind><unit>kmh</unit><now>36,0</now><max>72</max><azimuth>270</azimuth></wind>"
            b"<barometre><unit>hPa</unit><now>1015,4</now></barometre><rain>",
        )
    )
    item = normalize(data.records[0], data)
    assert item.observed_at == datetime(2026, 1, 15, 12, tzinfo=UTC)
    assert item.metrics["temperature"].value == 0
    assert item.metrics["humidity"].value is None
    assert item.metrics["wind_speed"].value == 10
    assert item.metrics["wind_speed"].original_value == 36
    assert item.metrics["wind_speed"].provider_quality == "0"
    assert "rain" not in item.metrics and "pressure_station" not in item.metrics
    reported = quality(data.records[0])
    assert reported["reported_fields"]["rain"]["total"] == "0.0"
    assert reported["reported_fields"]["wind"]["max"] == "72"
    assert reported["daily_period"] == "provider_day_timezone_unknown"
    assert item.metrics["rain_daily"].kind == "daily_counter"
    assert item.metrics["rain_daily"].value == 0
    assert item.metrics["pressure_sea_level"].kind == "sea_level_pressure"
    assert str(item.metrics["pressure_sea_level"].value) == "1015.4"
    latin = (
        XML.decode().replace("UTF-8", "ISO-8859-1").replace("Lugar inventado", "Ávila")
    )
    assert parse_xml(latin.encode("iso-8859-1")).records[0]["location"] == "Ávila"
    latin15 = latin.replace("ISO-8859-1", "ISO-8859-15").replace("Ávila", "Ávila €")
    assert parse_xml(latin15.encode("iso-8859-15")).records[0]["location"] == "Ávila €"


@pytest.mark.parametrize(
    "raw,code",
    [
        (
            b'<!DOCTYPE meteodata [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            + XML.split(b"?>")[1],
            "xml_dtd_forbidden",
        ),
        (
            b'<!DOCTYPE meteodata [<!ENTITY a "spam"><!ENTITY b "&a;&a;">]>'
            + XML.split(b"?>")[1],
            "xml_dtd_forbidden",
        ),
        (XML.decode().encode("utf-16"), "unsupported_xml_encoding"),
        (XML.replace(b"<unit>C", b"<unit>F"), "sensor_unit_changed"),
        (b"<html>upstream error</html>", "xml_contract_changed"),
        (b"<meteodata", "invalid_xml"),
    ],
)
def test_xml_rejects_hostile_or_incompatible_documents(raw, code):
    with pytest.raises(IngestionError, match=code):
        parse_xml(raw)


def test_station_clock_requires_timezone_and_distinguishes_dst_and_invalid_sentinels():
    data = parse_xml(XML)
    row = data.records[0]
    row["pubDate"] = "Sun, 25 Oct 2026 02:30:00 +0200"
    data = Batch([row], [], data.metadata_hash, datetime(2026, 10, 26, tzinfo=UTC))
    first = normalize(row, data).observed_at
    row["pubDate"] = "Sun, 25 Oct 2026 02:30:00 +0100"
    assert normalize(row, data).observed_at - first == timedelta(hours=1)
    row["pubDate"] = "Sun, 25 Oct 2026 02:30:00"
    with pytest.raises(ValueError):
        normalize(row, data)
    row["pubDate"] = "Sun, 25 Oct 2026 02:30:00 +0100"
    row["sensors"]["temperature"]["now"] = "-999"
    assert normalize(row, data).metrics["temperature"].value is None
    row["sensors"]["temperature"]["now"] = "N/D"
    with pytest.raises(ValueError):
        normalize(row, data)


def test_pending_terms_never_downloads_or_schedules(db, engine):
    calls = []
    adapter = MeteoclimaticAdapter(
        transport=httpx.MockTransport(lambda req: calls.append(req))
    )
    assert adapter.discover().status == "pending_terms"
    assert adapter.fetch_current().status == "pending_terms"
    with pytest.raises(IngestionError, match="pending_terms"):
        adapter.download()
    adapter.close()
    queue = Queue(
        engine,
        Settings(
            database_url=str(engine.url), _env_file=None, meteoclimatic_enabled=True
        ),
        "meteoclimatic",
    )
    queue.schedule()
    assert queue.claim() is None and count(db, Job) == 0 and calls == []
    with pytest.raises(IngestionError, match="pending_terms"):
        queue.resume()
    assert status(queue)["status"] == "pending_terms"


def test_single_download_discovery_idempotence_and_manual_location_resolution(mc, db):
    first = mc_commit(mc)
    assert first["new_sources"] == first["pending"] == 1 and first["inserted"] == 0
    source = db.scalar(select(StationSource))
    identity, first_seen = source.id, source.first_seen_at
    assert source.review_reason == "missing_coordinates"
    manual(db)
    calls = []

    def response(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=XML)

    # Make the ordinary combined current job due (catalog only reads metadata).
    db.execute(update(Job).values(next_run_at=db_now(db)))
    db.commit()
    claim = mc.claim()
    result = run_claim(
        mc,
        claim,
        adapter_factory=lambda **kw: MeteoclimaticAdapter(
            transport=httpx.MockTransport(response), **kw
        ),
    )
    assert result["result"]["inserted"] == 1 and len(calls) == 1
    second = mc_commit(mc)
    db.expire_all()
    assert second["unchanged"] == 1 and second["new_sources"] == 0
    assert count(db, StationSource) == count(db, Station) == count(db, Observation) == 1
    assert source.id == identity and source.first_seen_at == first_seen
    assert source.last_seen_at >= first_seen
    assert count(db, Job) == 2


def test_geography_all_provinces_outside_prefix_and_minute_boundary(mc, db):
    places = [
        (ID, "40.4168", "-3.7038", "28"),
        ("ESCYL0500000000000A", "40.6564", "-4.7009", "05"),
        ("ESCYL4000000000000A", "40.9429", "-4.1184", "40"),
        ("ESCLM1900000000000A", "40.633", "-3.164", "19"),
    ]
    for identity, lat, lon, code in places:
        manual(db, identity, lat, lon, precision="minute")
        source = db.scalar(
            select(StationSource).where(StationSource.external_id == identity)
        )
        assert db.get(Station, source.station_id).province_code == code
    assert manual(db, "ESMAD2800000000001A", "41.39", "2.17")["outside"] == 1
    # Accepted minute coordinates stay visible even on a real province boundary.
    from meteocentro.catalog import location
    from meteocentro.domain.provinces import load_provinces

    poly = load_provinces()["28"]
    lon, lat = (
        next(iter(poly.geoms)).exterior.coords[0]
        if poly.geom_type == "MultiPolygon"
        else poly.exterior.coords[0]
    )
    code, reason = location({"longitude": lon, "latitude": lat, "precision": "minute"})
    assert code in {"05", "19", "28", "40"} and reason is None
    assert count(db, StationSource) == 4


@pytest.mark.parametrize("target", ["station", "source", "identity"])
def test_exclusions_survive_repeat_restart_and_manual_registration(mc, db, target):
    manual(db)
    mc_commit(mc)
    source = db.scalar(select(StationSource))
    if target == "identity":
        db.add(IdentityExclusion(provider_id=source.provider_id, external_id=ID))
    else:
        db.add(
            Exclusion(
                **{
                    f"{target}_id": source.station_id
                    if target == "station"
                    else source.id
                }
            )
        )
    db.commit()
    restarted = Queue(mc.engine, mc.settings, "meteoclimatic")
    restarted.schedule()
    assert mc_commit(restarted)["excluded"] == 1
    assert manual(db)["excluded"] == 1
    assert count(db, Observation) == count(db, StationSource) == 1
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 0
        for suffix in (
            "",
            "/latest",
            "/observations?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z",
            "/daily-summaries?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z",
        ):
            assert (
                client.get(f"/api/v1/stations/{source.station_id}{suffix}").status_code
                == 404
            )


def test_identity_excluded_before_first_discovery(mc, db):
    db.add(IdentityExclusion(provider_id=provider(db).id, external_id=ID))
    db.commit()
    assert mc_commit(mc)["excluded"] == 1
    assert count(db, StationSource) == count(db, Station) == 0


def test_duplicate_suggestions_keep_series_separate_and_link_inherits_exclusion(mc, db):
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    commit_batch(aemet)
    original = db.scalar(select(StationSource))
    db.add(Exclusion(station_id=original.station_id))
    db.commit()
    result = manual(db, lat=str(ROW["lat"]), lon=str(ROW["lon"]))
    assert result["potential_duplicates"] == 1
    second = db.scalar(select(StationSource).where(StationSource.external_id == ID))
    assert second.station_id != original.station_id
    assert db.get(Station, second.station_id).moderation_status == "review"
    assert count(db, DuplicateCandidate) == 1
    assert mc_commit(mc)["inserted"] == 0
    link_source(db, second.id, original.station_id, "synthetic identity evidence")
    db.commit()
    assert mc_commit(mc)["excluded"] == 1
    assert count(db, Observation) == 3  # AEMET archive retained, no MC rain merged.


def test_reappearing_new_id_near_excluded_source_requires_review(mc, db):
    manual(db)
    source = db.scalar(select(StationSource))
    db.add(Exclusion(source_id=source.id))
    db.commit()
    result = manual(db, "ESMAD2800000000002B")
    assert result["potential_duplicates"] == 1 and result["pending"] == 1
    assert count(db, StationSource) == 2


def duplicate_fixture(
    mc,
    db,
    *,
    distance=1500,
    precision="minute",
    same_name=False,
    lat="40.45",
    lon="-3.70",
):
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    original = Station(
        name="Original",
        latitude=Decimal(lat),
        longitude=Decimal(lon),
        province_code="28",
        moderation_status="active",
    )
    db.add(original)
    db.flush()
    first = StationSource(
        station_id=original.id,
        provider_id=provider(db, "aemet").id,
        external_id="TEST1",
        latitude=original.latitude,
        longitude=original.longitude,
        status="enabled",
    )
    db.add(first)
    db.flush()
    register_manual(
        db,
        provider(db),
        ID,
        name="Original" if same_name else "Otra estación",
        latitude=str(float(lat) + math.degrees(distance / 6371000)),
        longitude=lon,
        precision=precision,
        evidence="synthetic position",
    )
    db.commit()
    second = db.scalar(select(StationSource).where(StationSource.external_id == ID))
    return first, second


@pytest.mark.parametrize(
    "distance,precision,same_name,expected",
    [
        (999, "minute", False, 1),
        (1001, "minute", False, 0),
        (999, "exact", True, 1),
        (1001, "exact", True, 0),
        (249, "exact", False, 1),
        (251, "exact", False, 0),
    ],
)
def test_duplicate_radius_one_km_keeps_exact_position_threshold(
    mc, db, distance, precision, same_name, expected
):
    _, source = duplicate_fixture(
        mc, db, distance=distance, precision=precision, same_name=same_name
    )
    assert count(db, DuplicateCandidate) == expected
    assert (source.review_reason == "potential_duplicate") == bool(expected)


@pytest.mark.parametrize(
    "protection",
    [
        None,
        "station",
        "source",
        "identity",
        "disabled",
        "relocation",
        "other_candidate",
        "outside",
    ],
)
def test_recheck_old_duplicate_radius_preserves_reviews_exclusions_and_is_idempotent(
    mc, db, protection
):
    first, source = duplicate_fixture(mc, db)
    station = db.get(Station, source.station_id)
    station.moderation_status = "review"
    source.review_reason = "potential_duplicate"
    pair = sorted((first.id, source.id))
    candidate = DuplicateCandidate(
        source_id=pair[0], other_source_id=pair[1], distance_m=1500, reason="proximity"
    )
    db.add(candidate)
    if protection in {"station", "source"}:
        db.add(
            Exclusion(
                **{
                    f"{protection}_id": station.id
                    if protection == "station"
                    else source.id
                }
            )
        )
    elif protection == "identity":
        db.add(
            IdentityExclusion(
                provider_id=source.provider_id, external_id=source.external_id
            )
        )
    elif protection == "disabled":
        source.status = "unavailable"
    elif protection == "relocation":
        source.source_metadata = {
            **source.source_metadata,
            "proposed_location": {"latitude": "41"},
        }
    elif protection == "other_candidate":
        other_station = Station(
            name="Otro",
            moderation_status="active",
            province_code="28",
            latitude=source.latitude,
            longitude=source.longitude,
        )
        db.add(other_station)
        db.flush()
        other = StationSource(
            station_id=other_station.id,
            provider_id=first.provider_id,
            external_id="TEST2",
            latitude=source.latitude,
            longitude=source.longitude,
            status="enabled",
        )
        db.add(other)
        db.flush()
        second_pair = sorted((other.id, source.id))
        db.add(
            DuplicateCandidate(
                source_id=second_pair[0],
                other_source_id=second_pair[1],
                distance_m=0,
                reason="proximity",
            )
        )
    elif protection == "outside":
        source.latitude = station.latitude = Decimal("41.390000")
        source.longitude = station.longitude = Decimal("2.170000")
        station.province_code = None
    db.commit()
    result = recheck_duplicate_radius(db, "User changes radius to 1 km")
    db.commit()
    assert candidate.status == "outside_radius"
    assert result["retired_pairs"] == 1
    assert result["released"] == ([ID] if protection is None else [])
    assert station.moderation_status == ("active" if protection is None else "review")
    if protection == "outside":
        assert source.review_reason == "outside"
    audits = count(db, AuditEvent)
    assert recheck_duplicate_radius(db, "repeat")["retired_pairs"] == 0
    db.commit()
    assert count(db, AuditEvent) == audits


@pytest.mark.parametrize("exclusion", [None, "station", "source", "identity"])
def test_boundary_recheck_shows_approximate_station_without_restoring_exclusions(
    mc, db, exclusion
):
    manual(db, lat="40.700000", lon="-4.216667", precision="minute")
    source = db.scalar(select(StationSource).where(StationSource.external_id == ID))
    station = db.get(Station, source.station_id)
    assert station.moderation_status == "active" and station.province_code == "40"
    # Simulate a persisted review created by the retired uncertainty-box policy.
    source.review_reason = "uncertain_boundary"
    station.moderation_status = "review"
    station.province_code = None
    if exclusion == "identity":
        db.add(
            IdentityExclusion(
                provider_id=source.provider_id, external_id=source.external_id
            )
        )
    elif exclusion:
        db.add(
            Exclusion(
                **{
                    f"{exclusion}_id": station.id
                    if exclusion == "station"
                    else source.id
                }
            )
        )
    db.commit()
    result = recheck_boundary_locations(db, "User accepts approximate province")
    db.commit()
    assert result["released"] == ([ID] if exclusion is None else [])
    assert station.moderation_status == ("active" if exclusion is None else "review")
    assert source.source_metadata["precision"] == "minute"
    audits = count(db, AuditEvent)
    assert recheck_boundary_locations(db, "repeat")["released"] == []
    db.commit()
    assert count(db, AuditEvent) == audits
    assert mc_commit(mc)["inserted"] == (1 if exclusion is None else 0)
    if exclusion is None:
        db.expire_all()
        assert station.province_code == "40" and source.review_reason is None
        with client_for(db) as client:
            result = client.get(
                "/api/v1/map?network=meteoclimatic&metric=temperature"
            ).json()
        assert result["total"] == 1
        assert result["items"][0]["sources"][0]["coordinate_precision"] == "minute"


def test_identical_and_conflicting_feed_duplicates(mc, db):
    manual(db)
    data = parse_xml(XML)
    identical = Batch(data.records * 2, [], data.metadata_hash, data.fetched_at)
    assert mc_commit(mc, identical)["duplicate_records"] == 1
    changed = copy.deepcopy(data.records[0])
    changed["sensors"]["temperature"]["now"] = "35"
    conflict = Batch(
        [data.records[0], changed], [], data.metadata_hash, data.fetched_at
    )
    result = mc_commit(mc, conflict)
    assert result["conflicting_records"] == 1 and result["revised"] == 0
    assert count(db, Observation) == 1


@pytest.mark.parametrize(
    "http_status,code",
    [
        (403, "invalid_credentials"),
        (429, "rate_limited"),
        (503, "provider_transient"),
        (302, "unexpected_http"),
    ],
)
def test_http_failure_isolation_keeps_catalog_and_other_network(
    mc, db, http_status, code
):
    manual(db)
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    claim = mc.claim()
    result = run_claim(
        mc,
        claim,
        adapter_factory=lambda **kw: MeteoclimaticAdapter(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    http_status, headers={"Retry-After": "90"}, text="private body"
                )
            ),
            **kw,
        ),
    )
    assert result["code"] == code
    assert aemet.claim("current") is not None
    assert count(db, StationSource) == 1
    assert db.get(ProviderRuntime, claim.provider_id).day_calls == 1
    assert "private body" not in str(status(mc))


def test_network_budget_is_persistent_atomic_and_independent(mc, db):
    mc.settings.meteoclimatic_daily_http_budget = 1
    claim = mc.claim()

    def reserve():
        try:
            Queue(mc.engine, mc.settings, "meteoclimatic").reserve_http(claim)
            return "ok"
        except IngestionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: reserve(), range(4)))
    assert outcomes.count("ok") == 1 and outcomes.count("daily_budget") == 3
    mc.fail(claim, IngestionError("provider_transient"))
    db.execute(update(Job).values(next_run_at=db_now(db)))
    db.commit()
    with pytest.raises(IngestionError, match="daily_budget"):
        mc.reserve_http(mc.claim())
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    aemet.reserve_http(aemet.claim())


def test_disabled_network_cannot_claim_or_write_app_keeps_other_source(mc, db):
    manual(db, lat="40.633", lon="-3.164")
    mc_commit(mc)
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    commit_batch(aemet)
    with client_for(db) as client:
        assert client.get("/api/v1/stations").json()["total"] == 2
        db.rollback()
        claim = mc.claim()  # AEMET test helper also makes this job due.
        assert claim is not None
        mc.settings.meteoclimatic_enabled = False
        mc.schedule()
        with Session(mc.engine) as session, pytest.raises(LeaseLost):
            mc.fence(session, claim)
        assert mc.claim() is None
        assert client.get("/health/ready").status_code == 200
        networks = client.get("/api/v1/providers").json()["items"]
        assert {row["code"]: row["status"] for row in networks} == {
            "aemet": "verified",
            "meteoclimatic": "disabled",
        }
        assert "fixture-only-permission" not in str(networks)
        assert client.get("/api/v1/stations").json()["total"] == 1
    assert count(db, Observation) == 4
    assert (
        db.scalar(select(Provider.id).where(Provider.code.in_(["wu", "wunderground"])))
        is None
    )
    assert set(db.scalars(select(Job.dedupe_key))) == {
        "aemet:current",
        "aemet:inventory",
        "meteoclimatic:current",
        "meteoclimatic:catalog",
    }


def test_location_change_records_history_once_preserves_manual_state(mc, db):
    aemet = Queue(mc.engine, mc.settings)
    aemet.schedule()
    commit_batch(aemet)
    source = db.scalar(select(StationSource))
    initial_lon = source.longitude
    changed = batch([{**ROW, "lon": -3.72}])
    assert commit_batch(aemet, changed)["location_review"] == 1
    commit_batch(aemet, changed)
    db.expire_all()
    assert source.longitude == initial_lon
    assert count(db, StationLocationHistory) == 1
    assert db.get(Station, source.station_id).moderation_status == "review"


def test_exclusion_during_download_discards_data(mc, db):
    manual(db)
    source = db.scalar(select(StationSource))

    def response(request):
        with Session(mc.engine) as other, other.begin():
            other.add(Exclusion(station_id=source.station_id))
        return httpx.Response(200, content=XML)

    result = run_claim(
        mc,
        mc.claim(),
        adapter_factory=lambda **kw: MeteoclimaticAdapter(
            transport=httpx.MockTransport(response), **kw
        ),
    )
    assert result["result"]["excluded"] == 1 and count(db, Observation) == 0


def test_identity_exclusion_commits_while_new_catalog_waits(mc, db, monkeypatch):
    # Trigger takes the identical key lock before there is a source/station row.
    import meteocentro.meteoclimatic as module

    original = module.upsert_source
    started = threading.Event()
    writer_pid = []

    def capture(session, *args, **kwargs):
        writer_pid.append(session.scalar(select(func.pg_backend_pid())))
        started.set()
        return original(session, *args, **kwargs)

    monkeypatch.setattr(module, "upsert_source", capture)
    with Session(mc.engine) as blocker:
        blocker.add(IdentityExclusion(provider_id=provider(db).id, external_id=ID))
        blocker.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(mc_commit, mc)
            assert started.wait(5)
            deadline, blocked = time.monotonic() + 5, False
            while time.monotonic() < deadline:
                blocked = db.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND NOT granted)"
                    ),
                    {"pid": writer_pid[0]},
                )
                if blocked:
                    break
                time.sleep(0.01)
            try:
                assert blocked
            finally:
                blocker.commit()
            assert future.result(timeout=10)["excluded"] == 1
    assert count(db, StationSource) == 0


def test_daily_catalog_absence_review_never_deletes_archive(mc, db):
    manual(db)
    data = parse_xml(XML)
    mc_commit(mc, data)
    source = db.scalar(select(StationSource))
    for day in range(1, 4):
        empty = Batch(
            [], [], data.metadata_hash, data.fetched_at + timedelta(days=day, seconds=1)
        )
        result = mc_commit(mc, empty)
        assert result["absent_sources"] == 1
        # Replay the same successful feed cannot count as an extra daily absence.
        assert mc_commit(mc, empty)["absent_sources"] == 0
    db.expire_all()
    assert source.review_reason == "missing_in_feed"
    assert source.source_metadata["consecutive_absent_reviews"] == 3
    assert count(db, Observation) == count(db, StationSource) == 1
    assert db.get(Station, source.station_id).moderation_status == "active"
    returning = Batch(
        data.records, [], data.metadata_hash, data.fetched_at + timedelta(days=5)
    )
    mc_commit(mc, returning)
    db.expire_all()
    assert source.review_reason is None
    assert source.source_metadata["consecutive_absent_reviews"] == 0


def test_timeout_size_and_no_redirect_fetch():
    for transport, limit, code in (
        (
            httpx.MockTransport(lambda req: httpx.Response(200, content=XML)),
            5,
            "response_too_large",
        ),
        (
            httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("private"))
            ),
            8000000,
            "http_timeout",
        ),
    ):
        adapter = MeteoclimaticAdapter(
            terms_reference="fixture", transport=transport, max_bytes=limit
        )
        with pytest.raises(IngestionError, match=code):
            adapter.download()
        adapter.close()


def profile_html(identity=ID, latitude="40º 25' N", longitude="03º 42' W"):
    return (
        f'<html><meta charset="iso-8859-15"><h1>Estación ({identity})</h1>'
        f'<td class="est_dades">{latitude}&nbsp;&nbsp;{longitude}&nbsp;-&nbsp;650 m</td>'
        "</html>"
    ).encode("iso-8859-15")


def catalog_run(queue, handler, *, after_profile=None):
    from meteocentro.meteoclimatic_catalog import MeteoclimaticCatalog

    with Session(queue.engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.dedupe_key == "meteoclimatic:catalog")
            .values(next_run_at=db_now(session), status="pending")
        )
    claim = queue.claim("catalog")
    assert claim
    with_adapter = MeteoclimaticAdapter(
        terms_reference="fixture-only-permission",
        reserve=lambda: queue.reserve_http(claim),
        transport=httpx.MockTransport(handler),
    )
    try:
        result, cursor = MeteoclimaticCatalog(queue, claim, with_adapter).run(
            after_profile=after_profile
        )
        queue.succeed(claim, result, cursor)
        return result
    finally:
        with_adapter.close()


def test_profile_parser_identity_precision_and_ambiguous_or_invalid_positions():
    from meteocentro.meteoclimatic_catalog import parse_profile

    coordinates = parse_profile(profile_html(), ID)
    assert float(coordinates["latitude"]) == pytest.approx(40 + 25 / 60)
    assert float(coordinates["longitude"]) == pytest.approx(-3.7)
    assert coordinates["altitude_m"] == 650
    for raw in (
        profile_html("ESMAD2800000000001A"),
        profile_html(latitude="40º 61' N"),
        profile_html() + profile_html(),
        b"<h1>Login</h1>",
    ):
        with pytest.raises(ValueError):
            parse_profile(raw, ID)


def test_profile_worker_resolves_location_caches_and_ingests_with_attribution(mc, db):
    assert mc_commit(mc)["pending"] == 1
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            content=(
                b"User-agent: *\nDisallow: /feed/\n"
                if request.url.path == "/robots.txt"
                else profile_html()
            ),
        )

    result = catalog_run(mc, handler)
    assert result["located"] == 1 and len(calls) == 2
    assert mc_commit(mc)["inserted"] == 1
    assert mc_commit(mc)["unchanged"] == 1
    assert catalog_run(mc, handler)["profiles_checked"] == 0 and len(calls) == 2
    db.expire_all()
    source = db.scalar(select(StationSource))
    assert source.source_metadata["precision"] == "minute"
    assert source.source_metadata["location_method"] == "public_profile"
    item = db.scalar(select(Observation))
    assert item.quality["attribution"]["source_url"].endswith(ID)
    assert item.metrics["rain_daily"]["period_basis"] == "provider_day_timezone_unknown"
    assert item.period_start is None and item.period_end is None
    assert source.review_reason is None
    with client_for(db) as client:
        detail = client.get(f"/api/v1/stations/{source.station_id}").json()
        assert detail["sources"][0]["coordinate_precision"] == "minute"
        networks = client.get("/api/v1/providers").json()["items"]
        assert (
            networks[0]["license_url"]
            == "https://creativecommons.org/licenses/by-nc-nd/3.0/"
        )


def test_profile_robots_denial_makes_no_profile_request(mc, db):
    mc_commit(mc)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=b"User-agent: *\nDisallow: /perfil/\n")

    assert catalog_run(mc, handler)["robots_disallowed"] == 1
    assert calls == ["/robots.txt"]
    assert db.scalar(select(StationSource)).latitude is None


def test_profile_does_not_overwrite_manual_or_excluded_during_download(mc, db):
    mc_commit(mc)

    def handler(request):
        return httpx.Response(
            200,
            content=(
                b"User-agent: *\nAllow: /\n"
                if request.url.path == "/robots.txt"
                else profile_html()
            ),
        )

    source = db.scalar(select(StationSource))

    def exclude():
        with Session(mc.engine) as session, session.begin():
            session.add(
                Exclusion(
                    source_id=source.id, reason="excluded while downloading profile"
                )
            )

    assert catalog_run(mc, handler, after_profile=exclude)["excluded"] == 1
    db.expire_all()
    assert source.latitude is None and count(db, Observation) == 0
    # Manual coordinates bypass automatic profile collection entirely.
    db.execute(text("DELETE FROM exclusions"))
    db.commit()
    manual(db)
    assert catalog_run(
        mc, lambda _: pytest.fail("manual location must not be requested")
    ) == {"profiles_checked": 0}


def test_profile_relocation_keeps_canonical_position_and_history(mc, db):
    from collections import Counter
    from decimal import Decimal

    from meteocentro.meteoclimatic_catalog import apply_profile, parse_profile

    mc_commit(mc)
    coordinates = parse_profile(profile_html(), ID)
    apply_profile(db, provider(db), ID, coordinates, Counter())
    db.commit()
    source = db.scalar(select(StationSource))
    before = source.latitude
    counters = Counter()
    apply_profile(
        db, provider(db), ID, {**coordinates, "latitude": Decimal("40.5")}, counters
    )
    db.commit()
    assert source.latitude == before and source.review_reason == "location_changed"
    assert counters["location_review"] == 1 and count(db, StationLocationHistory) == 1
    assert mc_commit(mc)["inserted"] == 0


def test_catalog_http_quota_reserves_current_budget(mc, db):
    mc.settings.meteoclimatic_daily_http_budget = 112
    mc.settings.meteoclimatic_current_reserve = 110
    claim = mc.claim("catalog")
    mc.reserve_http(claim)
    mc.reserve_http(claim)
    with pytest.raises(IngestionError, match="daily_budget"):
        mc.reserve_http(claim)
    mc.succeed(claim, {}, {})
    current = mc.claim("current")
    mc.reserve_http(current)
    assert db.get(ProviderRuntime, current.provider_id).day_calls == 3


def test_daily_counter_reset_extremes_and_pressure_never_become_intervals(mc, db):
    manual(db)
    data = parse_xml(XML)
    row = data.records[0]
    row["sensors"]["barometre"] = {
        "unit": "hPa",
        "now": "1014.2",
        "min": "1008",
        "max": "1015",
    }
    for stamp, rain in (
        ("Wed, 15 Jan 2026 23:55:00 +0000", "12.4"),
        ("Thu, 16 Jan 2026 00:05:00 +0000", "0.0"),
    ):
        row["pubDate"] = stamp
        row["sensors"]["rain"]["total"] = rain
        assert mc_commit(mc, data)["inserted"] == 1
    rows = db.scalars(select(Observation).order_by(Observation.observed_at)).all()
    assert [r.metrics["rain_daily"]["value"] for r in rows] == ["12.4", "0.0"]
    assert all(r.period_start is None and r.period_end is None for r in rows)
    assert all(
        r.metrics["pressure_sea_level"]["kind"] == "sea_level_pressure" for r in rows
    )
    assert all(
        r.metrics["temperature_daily_max"]["kind"] == "daily_maximum" for r in rows
    )
    assert all(
        r.quality["reported_fields"]["rain"]["total"] in ("12.4", "0.0") for r in rows
    )


def test_profile_404_is_isolated_and_retry_is_persistent(mc, db):
    mc_commit(mc)

    def handler(request):
        return (
            httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
            if request.url.path == "/robots.txt"
            else httpx.Response(404)
        )

    assert catalog_run(mc, handler)["invalid_profiles"] == 1
    db.expire_all()
    source = db.scalar(select(StationSource))
    assert source.source_metadata["profile_error"] == "profile_unavailable"
    assert (
        catalog_run(mc, lambda _: pytest.fail("404 retried before deadline"))[
            "profiles_checked"
        ]
        == 0
    )
    assert provider(db).status == "verified"


def test_profile_can_complete_manual_id_without_coordinates(mc, db):
    manual(db, lat=None, lon=None)

    def handler(request):
        return httpx.Response(
            200,
            content=(
                b"User-agent: *\nAllow: /\n"
                if request.url.path == "/robots.txt"
                else profile_html()
            ),
        )

    assert catalog_run(mc, handler)["located"] == 1
    assert mc_commit(mc)["inserted"] == 1


def test_discovery_and_profile_preserve_explicit_review(mc, db):
    mc_commit(mc)
    source = db.scalar(select(StationSource))
    source.review_reason = "manual_review"
    db.commit()
    assert mc_commit(mc)["inserted"] == 0
    db.expire_all()
    assert source.review_reason == "manual_review"

    def handler(request):
        return httpx.Response(
            200,
            content=(
                b"User-agent: *\nAllow: /\n"
                if request.url.path == "/robots.txt"
                else profile_html()
            ),
        )

    assert catalog_run(mc, handler)["review"] == 1
    assert mc_commit(mc)["inserted"] == 0
    db.expire_all()
    assert db.get(Station, source.station_id).moderation_status == "review"
    assert source.review_reason == "manual_review"
