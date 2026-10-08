"""Forecasts for the «Previsiones» tab. Never mixed with observations or histories.

AEMET municipal forecasts run as an AEMET worker job, so every call is reserved from
the shared AEMET quota. Open-Meteo (GFS/ECMWF deterministic and ensembles) runs as a
local worker job: documented public API, no key, non-commercial use with attribution.
"""

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from meteocentro.aemet import BASE, checked_url, decode_json, digest
from meteocentro.ingestion_errors import IngestionError
from meteocentro.models import ForecastSnapshot, Job

MADRID = ZoneInfo("Europe/Madrid")
MAX_BYTES = 4_000_000


@dataclass(frozen=True)
class Location:
    code: str
    name: str
    municipality: str
    latitude: float
    longitude: float
    ensemble: bool
    aemet_url: str
    # Meteoalerta: autonomous community bundle and warning zone (see alerts.py).
    warning_area: str
    warning_zone: str


LOCATIONS = {
    location.code: location
    for location in (
        Location(
            "madrid",
            "Madrid",
            "28079",
            40.4165,
            -3.70256,
            True,
            "https://www.aemet.es/es/eltiempo/prediccion/municipios/madrid-id28079",
            "72",
            "722802",
        ),
        Location(
            "huetor-santillan",
            "Huétor de Santillán",
            "18099",
            37.22091,
            -3.51634,
            True,
            "https://www.aemet.es/es/eltiempo/prediccion/municipios/huetor-de-santillan-id18099",
            "61",
            "611801",
        ),
    )
}


def store(db: Session, source, location, product, payload, issued_at, fetched_at) -> bool:
    """Keep the latest snapshot; an older run or issue never replaces a newer one."""
    table = ForecastSnapshot.__table__
    statement = insert(ForecastSnapshot).values(
        source=source,
        location=location,
        product=product,
        issued_at=issued_at,
        fetched_at=fetched_at,
        payload=payload,
        payload_hash=digest(payload),
    )
    excluded = statement.excluded
    result = db.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.source, table.c.location, table.c.product],
            set_={
                "issued_at": excluded.issued_at,
                "fetched_at": excluded.fetched_at,
                "payload": excluded.payload,
                "payload_hash": excluded.payload_hash,
            },
            where=or_(
                table.c.issued_at.is_(None),
                excluded.issued_at.is_(None),
                excluded.issued_at >= table.c.issued_at,
            ),
        ).returning(table.c.source)
    )
    return result.first() is not None


# --- AEMET municipal forecast ------------------------------------------------------------

AEMET_PATHS = {
    "daily": "prediccion/especifica/municipio/diaria/{}",
    "hourly": "prediccion/especifica/municipio/horaria/{}",
}
PERIOD = re.compile(r"\d{2}-\d{2}")
ISSUED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def number(value, low, high):
    """AEMET sends numbers or numeric strings; "" means not forecast, never zero."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    if isinstance(value, str):
        if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", value.strip()):
            raise ValueError("invalid_number")
        value = value.strip().replace(",", ".")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_number") from None
    return result if low <= result <= high else None


def amount(value):
    """Hourly rain/snow: "Ip" is AEMET's «inapreciable» (trace), not a missing value."""
    if isinstance(value, str) and value.strip().lower() == "ip":
        return None, True
    return number(value, 0, 500), False


def sky(value, description):
    if value in (None, ""):
        return None, None, False
    if not isinstance(value, str) or not re.fullmatch(r"\d{2,3}n?", value):
        raise ValueError("invalid_sky")
    return value.rstrip("n"), str(description or "")[:80] or None, value.endswith("n")


def forecast_root(raw, location):
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("invalid_root")
    root = raw[0]
    if str(root.get("id")) != location.municipality:
        raise ValueError("wrong_municipality")
    days = (root.get("prediccion") or {}).get("dia")
    if not isinstance(days, list) or not days or not all(isinstance(d, dict) for d in days):
        raise ValueError("invalid_days")
    return root, days


def issued(root) -> datetime:
    value = root.get("elaborado")
    if not isinstance(value, str) or not ISSUED.fullmatch(value):
        raise ValueError("invalid_issue_time")
    # AEMET does not state a zone; like the rest of the municipal product, official time.
    return datetime.fromisoformat(value).replace(tzinfo=MADRID).astimezone(UTC)


def forecast_day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", value):
        raise ValueError("invalid_date")
    return date.fromisoformat(value[:10])


def entries(day, key):
    value = day.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ValueError("invalid_" + key)
    return value


def period_of(entry):
    value = entry.get("periodo", "00-24")
    if not isinstance(value, str) or not PERIOD.fullmatch(value):
        raise ValueError("invalid_period")
    return value


def extremes(day, key, low, high):
    block = day.get(key) or {}
    if not isinstance(block, dict):
        raise ValueError("invalid_" + key)
    return {
        "max": number(block.get("maxima"), low, high),
        "min": number(block.get("minima"), low, high),
    }


def daily_day(day) -> dict:
    periods: dict[str, dict] = {}

    def slot(entry):
        return periods.setdefault(period_of(entry), {"period": period_of(entry)})

    for entry in entries(day, "estadoCielo"):
        code, description, _night = sky(entry.get("value"), entry.get("descripcion"))
        slot(entry).update(sky_code=code, sky=description)
    for entry in entries(day, "probPrecipitacion"):
        slot(entry)["precipitation_probability"] = number(entry.get("value"), 0, 100)
    for entry in entries(day, "cotaNieveProv"):
        slot(entry)["snow_level"] = number(entry.get("value"), 0, 5000)
    for entry in entries(day, "viento"):
        direction = entry.get("direccion")
        slot(entry).update(
            wind_direction=direction if isinstance(direction, str) and direction else None,
            wind_speed=number(entry.get("velocidad"), 0, 300),
        )
    for entry in entries(day, "rachaMax"):
        slot(entry)["gust"] = number(entry.get("value"), 0, 400)
    temperature = extremes(day, "temperatura", -60, 60)
    points = (day.get("temperatura") or {}).get("dato") or []
    temperature["at"] = [
        {"hour": p.get("hora"), "value": number(p.get("value"), -60, 60)}
        for p in points
        if isinstance(p, dict) and isinstance(p.get("hora"), int)
    ]
    return {
        "date": forecast_day(day.get("fecha")).isoformat(),
        # Longest period first: 00-24, then halves, then quarters.
        "periods": sorted(
            periods.values(),
            key=lambda p: (-(int(p["period"][3:]) - int(p["period"][:2])), p["period"]),
        ),
        "temperature": temperature,
        "feels_like": extremes(day, "sensTermica", -80, 70),
        "humidity": extremes(day, "humedadRelativa", 0, 100),
        "uv_max": number(day.get("uvMax"), 0, 20),
    }


def normalize_aemet_daily(raw, location: Location) -> tuple[dict, datetime]:
    root, raw_days = forecast_root(raw, location)
    days = [daily_day(day) for day in raw_days]
    when = issued(root)
    return {
        "name": location.name,
        "issued_at": when.isoformat(),
        "link": location.aemet_url,
        "days": days,
    }, when


def local_instant(day: date, hour: int, fold: int) -> datetime | None:
    if not 0 <= hour <= 24:
        raise ValueError("invalid_hour")
    naive = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
    instant = naive.replace(tzinfo=MADRID, fold=fold)
    # Hours skipped by the spring change do not exist; they are omitted, never invented.
    if instant.astimezone(UTC).astimezone(MADRID).replace(tzinfo=None) != naive:
        return None
    return instant.astimezone(UTC)


def hourly_entries(day, key):
    """Yield (instant, entry) using official hours; a repeated hour is the autumn fold."""
    day_value = forecast_day(day.get("fecha"))
    seen: set[str] = set()
    for entry in entries(day, key):
        label = entry.get("periodo")
        if not isinstance(label, str) or not re.fullmatch(r"\d{2}", label):
            raise ValueError("invalid_hour")
        instant = local_instant(day_value, int(label), 1 if label in seen else 0)
        seen.add(label)
        if instant is not None:
            yield instant, entry


def window(day_value: date, label) -> tuple[datetime, datetime] | None:
    if not isinstance(label, str) or not re.fullmatch(r"\d{4}", label):
        raise ValueError("invalid_window")
    start, end = int(label[:2]), int(label[2:])
    begin = local_instant(day_value, start, 0)
    finish = local_instant(day_value + timedelta(days=1 if end <= start else 0), end, 0)
    return (begin, finish) if begin and finish else None


def normalize_aemet_hourly(raw, location: Location) -> tuple[dict, datetime]:
    root, raw_days = forecast_root(raw, location)
    hours: dict[datetime, dict] = {}
    windows: dict[tuple[datetime, datetime], dict] = {}
    sun = []

    def hour(instant):
        return hours.setdefault(instant, {"time": instant.isoformat()})

    for day in raw_days:
        day_value = forecast_day(day.get("fecha"))
        sun.append(
            {
                "date": day_value.isoformat(),
                "sunrise": day.get("orto") if isinstance(day.get("orto"), str) else None,
                "sunset": day.get("ocaso") if isinstance(day.get("ocaso"), str) else None,
            }
        )
        for instant, entry in hourly_entries(day, "estadoCielo"):
            code, description, night = sky(entry.get("value"), entry.get("descripcion"))
            hour(instant).update(sky_code=code, sky=description, night=night)
        for key, field, low, high in (
            ("temperatura", "temperature", -60, 60),
            ("sensTermica", "feels_like", -80, 70),
            ("humedadRelativa", "humidity", 0, 100),
        ):
            for instant, entry in hourly_entries(day, key):
                hour(instant)[field] = number(entry.get("value"), low, high)
        for key, field in (("precipitacion", "precipitation"), ("nieve", "snow")):
            for instant, entry in hourly_entries(day, key):
                value, trace = amount(entry.get("value"))
                hour(instant).update({field: value, field + "_trace": trace})
        for instant, entry in hourly_entries(day, "vientoAndRachaMax"):
            if "direccion" in entry:
                direction, speed = entry.get("direccion"), entry.get("velocidad")
                if not isinstance(direction, list) or not isinstance(speed, list):
                    raise ValueError("invalid_wind")
                hour(instant).update(
                    wind_direction=direction[0] if direction and direction[0] else None,
                    wind_speed=number(speed[0], 0, 300) if speed else None,
                )
            else:
                hour(instant)["gust"] = number(entry.get("value"), 0, 400)
        for key, field in (
            ("probPrecipitacion", "precipitation"),
            ("probTormenta", "storm"),
            ("probNieve", "snow"),
        ):
            for entry in entries(day, key):
                span = window(day_value, entry.get("periodo"))
                if span:
                    windows.setdefault(
                        span, {"start": span[0].isoformat(), "end": span[1].isoformat()}
                    )[field] = number(entry.get("value"), 0, 100)
    when = issued(root)
    return {
        "name": location.name,
        "issued_at": when.isoformat(),
        "link": location.aemet_url.replace("/municipios/", "/municipios/horas/"),
        "hours": [hours[k] for k in sorted(hours)],
        "windows": [windows[k] for k in sorted(windows)],
        "sun": sun,
    }, when


AEMET_NORMALIZERS = {"daily": normalize_aemet_daily, "hourly": normalize_aemet_hourly}


def run_aemet_forecast(queue, claim, adapter):
    """AEMET worker job: two-step download, quota reserved by the adapter per request."""
    if not adapter.key:
        raise IngestionError("pending_access", pause=True)
    saved = unchanged = 0
    for location in LOCATIONS.values():
        for product, path in AEMET_PATHS.items():
            url = BASE + path.format(location.municipality)
            envelope = adapter.request(url, authenticated=True)
            if not isinstance(envelope, dict):
                raise IngestionError("invalid_envelope")
            raw = adapter.request(checked_url(envelope.get("datos")))
            try:
                payload, issued_at = AEMET_NORMALIZERS[product](raw, location)
            except (ValueError, TypeError, AttributeError):
                # A contract change only affects forecasts; observations keep running.
                raise IngestionError("forecast_contract_changed") from None
            with Session(queue.engine) as db, db.begin():
                queue.fence(db, claim)
                if store(
                    db, "aemet", location.code, product, payload, issued_at, datetime.now(UTC)
                ):
                    saved += 1
                else:
                    unchanged += 1
    return {"forecasts_saved": saved, "forecasts_older": unchanged}, {}


# --- Open-Meteo models ------------------------------------------------------------------

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
META_URL = "https://api.open-meteo.com/data/{}/static/meta.json"


@dataclass(frozen=True)
class Model:
    code: str
    request: str
    suffix: str
    meta: str
    label: str
    days: int


DETERMINISTIC = (
    Model("gfs", "gfs_global", "gfs_global", "ncep_gfs025", "GFS (NOAA) 0,25°", 16),
    Model("ecmwf", "ecmwf_ifs025", "ecmwf_ifs025", "ecmwf_ifs025", "ECMWF IFS 0,25°", 15),
)
ENSEMBLES = (
    Model("gfs", "gfs05", "ncep_gefs05", "ncep_gefs05", "GEFS (NOAA) 0,5° · 31 miembros", 16),
    Model(
        "ecmwf",
        "ecmwf_ifs025",
        "ecmwf_ifs025_ensemble",
        "ecmwf_ifs025_ensemble",
        "ECMWF ENS 0,25° · 51 miembros",
        15,
    ),
)
INSTANT = (
    "temperature_2m",
    "dew_point_2m",
    "temperature_850hPa",
    "pressure_msl",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
)
ACCUMULATED = ("precipitation", "snowfall")
EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "dew_point_2m": "°C",
    "temperature_850hPa": "°C",
    "pressure_msl": "hPa",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
    "wind_direction_10m": "°",
    "cloud_cover_low": "%",
    "cloud_cover_mid": "%",
    "cloud_cover_high": "%",
    "precipitation": "mm",
    "snowfall": "cm",
}
STEP = 3 * 3600  # Instantaneous values: every 3 h, native for both models up to 144 h.
BIN = 6 * 3600  # Precipitation: 6 h totals ending at 00/06/12/18 UTC.


def times_of(hourly) -> list[int]:
    values = hourly.get("time") if isinstance(hourly, dict) else None
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(t, int) and not isinstance(t, bool) for t in values)
        or any(b - a != 3600 for a, b in zip(values, values[1:], strict=False))
    ):
        raise ValueError("invalid_time_axis")
    return values


def series(hourly, key, length):
    values = hourly.get(key)
    if not isinstance(values, list) or len(values) != length:
        raise ValueError("missing_" + key)
    for value in values:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int | float) or value != value
        ):
            raise ValueError("invalid_" + key)
    return values


def sampled(times, values, step):
    return [(t, v) for t, v in zip(times, values, strict=True) if t % step == 0]


def binned(times, values):
    """Sum hourly totals into 6 h windows; any missing hour leaves the window unknown."""
    index = {t: v for t, v in zip(times, values, strict=True)}
    result = []
    for end in (t for t in times if t % BIN == 0):
        hours = [index.get(end - h * 3600, "absent") for h in range(6)]
        if "absent" in hours:
            continue  # Window starts before the returned range.
        result.append((end, None if None in hours else round(sum(hours), 2)))
    return result


def trimmed(pairs_by_key: dict[str, list[tuple[int, float | None]]]):
    """Drop the tail where every series is empty (each model has its own horizon)."""
    keys = list(pairs_by_key)
    if not keys:
        return [], {}
    axis = [t for t, _ in pairs_by_key[keys[0]]]
    last = -1
    for i in range(len(axis)):
        if any(pairs_by_key[k][i][1] is not None for k in keys):
            last = i
    return axis[: last + 1], {k: [v for _, v in pairs_by_key[k][: last + 1]] for k in keys}


def check_units(document, keys):
    units = document.get("hourly_units")
    if not isinstance(units, dict):
        raise ValueError("missing_units")
    for key, variable in keys:
        if units.get(key) != EXPECTED_UNITS[variable]:
            raise ValueError("unexpected_unit_" + key)


def grid(document):
    return {
        key: document.get(key) if isinstance(document.get(key), int | float) else None
        for key in ("latitude", "longitude", "elevation")
    }


def normalize_deterministic(document, model: Model, run: datetime | None) -> dict:
    hourly = document.get("hourly")
    times = times_of(hourly)
    keys = [(f"{v}_{model.suffix}", v) for v in (*INSTANT, *ACCUMULATED)]
    check_units(document, keys)
    raw = {v: series(hourly, key, len(times)) for key, v in keys}
    axis, instant = trimmed({v: sampled(times, raw[v], STEP) for v in INSTANT})
    rain_axis, rain = trimmed({v: binned(times, raw[v]) for v in ACCUMULATED})
    if not axis:
        raise ValueError("empty_model")
    return {
        "model": model.code,
        "label": model.label,
        "run": run.isoformat() if run else None,
        "grid": grid(document),
        "time": axis,
        "series": instant,
        "accumulated_6h": {"time": rain_axis, **rain},
    }


def member_keys(hourly, variable, suffix):
    control = f"{variable}_{suffix}"
    pattern = re.compile(rf"{re.escape(variable)}_member(\d{{2}})_{re.escape(suffix)}")
    members = sorted((int(m.group(1)), key) for key in hourly if (m := pattern.fullmatch(key)))
    if control not in hourly or len(members) < 10:
        raise ValueError("missing_members_" + variable)
    return [control] + [key for _, key in members]


def normalize_ensemble(document, model: Model, run: datetime | None) -> dict:
    hourly = document.get("hourly")
    times = times_of(hourly)
    t850 = member_keys(hourly, "temperature_850hPa", model.suffix)
    rain = member_keys(hourly, "precipitation", model.suffix)
    if len(t850) != len(rain):
        raise ValueError("member_mismatch")
    check_units(
        document,
        [(k, "temperature_850hPa") for k in t850] + [(k, "precipitation") for k in rain],
    )
    # Synoptic hours only (00/06/12/18 UTC): native steps for every member and horizon.
    t_axis, t_members = trimmed(
        {k: sampled(times, series(hourly, k, len(times)), BIN) for k in t850}
    )
    r_axis, r_members = trimmed({k: binned(times, series(hourly, k, len(times))) for k in rain})

    def mean(columns):
        result = []
        for values in zip(*columns, strict=True):
            # A mean over a partial ensemble would change meaning silently.
            result.append(None if None in values else round(sum(values) / len(values), 2))
        return result

    t_values = [t_members[k] for k in t850]
    r_values = [r_members[k] for k in rain]
    return {
        "model": model.code,
        "label": model.label,
        "run": run.isoformat() if run else None,
        "grid": grid(document),
        "members": len(t850),
        "temperature_850hPa": {"time": t_axis, "members": t_values, "mean": mean(t_values)},
        "precipitation_6h": {"time": r_axis, "members": r_values, "mean": mean(r_values)},
    }


class OpenMeteo:
    HOSTS = {"api.open-meteo.com", "ensemble-api.open-meteo.com"}

    def __init__(self, transport=None, max_bytes=MAX_BYTES):
        self.max_bytes = max_bytes
        self.http = httpx.Client(
            timeout=httpx.Timeout(30, connect=10, pool=10, write=10),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "Meteocentro/0.2", "Accept": "application/json"},
        )

    def close(self):
        self.http.close()

    def get(self, url, params=None):
        if httpx.URL(url).host not in self.HOSTS:
            raise IngestionError("unsafe_data_url")
        try:
            started = time.monotonic()
            with self.http.stream("GET", url, params=params) as response:
                if response.status_code == 429:
                    raise IngestionError("rate_limited")
                if response.status_code == 400:
                    raise IngestionError("invalid_request")
                if response.status_code != 200:
                    raise IngestionError(
                        "provider_transient" if response.status_code >= 500 else "unexpected_http"
                    )
                chunks, length = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    length += len(chunk)
                    if length > self.max_bytes:
                        raise IngestionError("response_too_large")
                    if time.monotonic() - started > 60:
                        raise IngestionError("download_deadline")
                    chunks.append(chunk)
                document = decode_json(b"".join(chunks))
        except httpx.TimeoutException:
            raise IngestionError("http_timeout") from None
        except httpx.HTTPError:
            raise IngestionError("http_transport") from None
        if not isinstance(document, dict) or document.get("error"):
            raise IngestionError("invalid_request")
        return document

    def run(self, model: Model) -> datetime | None:
        """Initialisation time of the run served, from Open-Meteo's model metadata."""
        try:
            value = self.get(META_URL.format(model.meta)).get("last_run_initialisation_time")
        except IngestionError:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None
        return datetime.fromtimestamp(value, UTC)

    def point(self, url, location: Location, models, variables):
        return self.get(
            url,
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "hourly": ",".join(variables),
                "models": ",".join(m.request for m in models),
                "forecast_days": max(m.days for m in models),
                "timezone": "UTC",
                "timeformat": "unixtime",
            },
        )


def fetch_open_meteo(client: OpenMeteo):
    """Yield (location, product, payload, run) or (location, product, error_code)."""
    runs = {("det", m.code): client.run(m) for m in DETERMINISTIC}
    runs |= {("ens", m.code): client.run(m) for m in ENSEMBLES}
    jobs = [
        (location, "deterministic", FORECAST_URL, DETERMINISTIC, (*INSTANT, *ACCUMULATED))
        for location in LOCATIONS.values()
    ] + [
        (location, "ensemble", ENSEMBLE_URL, ENSEMBLES, ("temperature_850hPa", "precipitation"))
        for location in LOCATIONS.values()
        if location.ensemble
    ]
    for location, kind, url, models, variables in jobs:
        try:
            document = client.point(url, location, models, variables)
        except IngestionError as error:
            for model in models:
                yield location, f"{kind}_{model.code}", None, None, error.code
            continue
        for model in models:
            run = runs[("det" if kind == "deterministic" else "ens", model.code)]
            normalize = normalize_deterministic if kind == "deterministic" else normalize_ensemble
            try:
                payload = normalize(document, model, run)
            except (ValueError, TypeError, AttributeError):
                yield location, f"{kind}_{model.code}", None, None, "forecast_contract_changed"
                continue
            yield location, f"{kind}_{model.code}", payload, run, None


OPEN_METEO_JOB = "local:open-meteo-forecast"
RETRY_SECONDS = 600


def refresh_open_meteo(engine, settings, *, transport=None, force=False):
    """Local worker job. Returns a report when it ran, None when not due or disabled."""
    if not settings.open_meteo_enabled:
        return None
    interval = settings.open_meteo_forecast_seconds
    with Session(engine) as db, db.begin():
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(746302008)")):
            return None
        now = db.scalar(select(func.clock_timestamp())).astimezone(UTC)
        job = db.scalar(select(Job).where(Job.dedupe_key == OPEN_METEO_JOB).with_for_update())
        if job is None:
            job = Job(
                kind="open_meteo_forecast",
                status="local",
                dedupe_key=OPEN_METEO_JOB,
                next_run_at=now,
                interval_seconds=interval,
                cursor={},
            )
            db.add(job)
        elif job.next_run_at > now and not force:
            return None
        # Claim before any HTTP; a crash waits for the next interval instead of looping.
        job.next_run_at = now + timedelta(seconds=interval)
        job.interval_seconds = interval
        db.flush()
        job_id = job.id
    client = OpenMeteo(transport=transport)
    saved, errors = [], {}
    try:
        for location, product, payload, run, error in fetch_open_meteo(client):
            name = f"{location.code}/{product}"
            if error:
                errors[name] = error
                continue
            with Session(engine) as db, db.begin():
                if store(db, "open_meteo", location.code, product, payload, run, datetime.now(UTC)):
                    saved.append(name)
    finally:
        client.close()
    with Session(engine) as db, db.begin():
        job = db.get(Job, job_id, with_for_update=True)
        now = db.scalar(select(func.clock_timestamp())).astimezone(UTC)
        cursor = dict(job.cursor or {})
        cursor["errors"] = errors
        if saved:
            cursor["last_success_at"] = now.isoformat()
        if errors:
            job.next_run_at = min(job.next_run_at, now + timedelta(seconds=RETRY_SECONDS))
        job.cursor = cursor
    return {"status": "partial" if errors else "succeeded", "saved": saved, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Refresco manual de previsiones Open-Meteo")
    parser.add_argument("--open-meteo-now", action="store_true", help="ignora el intervalo")
    args = parser.parse_args()
    from meteocentro.config import get_settings
    from meteocentro.db import get_engine

    report = refresh_open_meteo(get_engine(), get_settings(), force=args.open_meteo_now)
    print(json.dumps(report or {"status": "not_due_or_disabled"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
