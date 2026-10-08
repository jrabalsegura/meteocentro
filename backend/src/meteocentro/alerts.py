"""AEMET Meteoalerta warnings (CAP 1.2) for the forecast places.

Each «último elaborado» bundle of an autonomous community is a complete snapshot of the
messages in force: yellow, orange and red warnings until they expire, and explicit green
messages for the newest day. Within the days that snapshot covers, a zone without a
warning has none; a missing or stale snapshot is unknown, never green.
"""

import io
import tarfile
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from meteocentro.aemet import BASE, checked_url
from meteocentro.forecast import LOCATIONS, MADRID, Location, store
from meteocentro.ingestion_errors import IngestionError
from meteocentro.meteoclimatic import NoDTD

PATH = "avisos_cap/ultimoelaborado/area/{}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
LEVELS = ("verde", "amarillo", "naranja", "rojo")
DAYS = 3  # AEMET warns for today, tomorrow and the day after.
MAX_FILES = 2000
MAX_FILE_BYTES = 2_000_000
PAGE = "https://www.aemet.es/es/eltiempo/prediccion/avisos"


def text(node, tag) -> str:
    return (node.findtext(CAP + tag) or "").strip()


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("naive_time")
    return result.astimezone(UTC)


def named(node, tag) -> dict[str, str]:
    return {text(item, "valueName"): text(item, "value") for item in node.findall(CAP + tag)}


def files(raw: bytes) -> list[bytes]:
    """XML members of the bundle, read in memory only; nothing is written to disk."""
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            result = []
            for member in archive:
                if not member.isfile() or not member.name.endswith(".xml"):
                    continue
                if len(result) >= MAX_FILES or member.size > MAX_FILE_BYTES:
                    raise ValueError("bundle_too_large")
                result.append(archive.extractfile(member).read())
            return result
    except (tarfile.TarError, EOFError, OSError):
        raise ValueError("invalid_bundle") from None


def message(raw: bytes) -> dict | None:
    try:
        root = ET.fromstring(raw, parser=ET.XMLParser(target=NoDTD()))
    except ET.ParseError:
        raise ValueError("invalid_xml") from None
    if root.tag != CAP + "alert":
        raise ValueError("not_cap")
    if text(root, "status") != "Actual":
        return None  # Exercises and tests are never shown.
    kind = text(root, "msgType")
    if kind not in ("Alert", "Update", "Cancel"):
        raise ValueError("invalid_msg_type")
    spanish = [i for i in root.findall(CAP + "info") if text(i, "language") == "es-ES"]
    if len(spanish) != 1:
        raise ValueError("missing_spanish_info")
    info = spanish[0]
    parameters = named(info, "parameter")
    level = parameters.get("AEMET-Meteoalerta nivel")
    if level not in LEVELS:
        raise ValueError("invalid_level")
    phenomenon = named(info, "eventCode").get("AEMET-Meteoalerta fenomeno", "")
    code, _, name = phenomenon.partition(";")
    if not code or not name:
        raise ValueError("invalid_phenomenon")
    zones = {}
    for area in info.findall(CAP + "area"):
        zone = named(area, "geocode").get("AEMET-Meteoalerta zona")
        if zone:
            zones[zone] = text(area, "areaDesc") or None
    # "P1;Precipitación acumulada en una hora;15 mm" -> threshold shown to people.
    threshold = parameters.get("AEMET-Meteoalerta parametro", "").split(";")
    expires = instant(text(info, "expires"))
    return {
        "identifier": text(root, "identifier"),
        "references": {
            reference.split(",")[1]
            for reference in text(root, "references").split()
            if reference.count(",") == 2
        },
        "kind": kind,
        "sent": instant(text(root, "sent")),
        "phenomenon": name.strip()[:60],
        "phenomenon_code": code.strip()[:4],
        "level": level,
        "onset": instant(text(info, "onset") or text(info, "effective")),
        "expires": expires,
        "zones": zones,
        "threshold": f"{threshold[1]}: {threshold[2]}"[:120] if len(threshold) == 3 else None,
        "probability": parameters.get("AEMET-Meteoalerta probabilidad") or None,
        "description": text(info, "description")[:600] or None,
        "instruction": text(info, "instruction")[:600] or None,
    }


def local_date(value: datetime) -> date:
    return value.astimezone(MADRID).date()


def normalize_warnings(raw: bytes, locations, fetched_at: datetime) -> dict[str, tuple]:
    """Return {location code: (payload, issued_at)} for one autonomous community bundle."""
    messages = [m for m in map(message, files(raw)) if m]
    if not messages:
        raise ValueError("empty_bundle")
    superseded = {ref for m in messages for ref in m["references"]}
    issued_at = max(m["sent"] for m in messages)
    result = {}
    for location in locations:
        zone = location.warning_zone
        mine = [m for m in messages if zone in m["zones"]]
        # Every bundle names every zone (green messages at least); otherwise it is not
        # the complete snapshot this module relies on.
        if not mine:
            raise ValueError("zone_missing")
        current = [
            m
            for m in mine
            if m["kind"] != "Cancel"
            and m["identifier"] not in superseded
            and m["expires"] > fetched_at
        ]
        warnings = sorted(
            (
                {
                    "phenomenon": m["phenomenon"],
                    "phenomenon_code": m["phenomenon_code"],
                    "level": m["level"],
                    "onset": m["onset"].isoformat(),
                    "expires": m["expires"].isoformat(),
                    "threshold": m["threshold"],
                    "probability": m["probability"],
                    "description": m["description"],
                    "instruction": m["instruction"],
                }
                for m in current
                if m["level"] != "verde"
            ),
            key=lambda w: (w["onset"], -LEVELS.index(w["level"]), w["phenomenon"]),
        )
        # Last official day the snapshot speaks about for this zone (green or not).
        covered = max(local_date(m["expires"] - timedelta(seconds=1)) for m in mine)
        result[location.code] = (
            {
                "zone": zone,
                "zone_name": next((m["zones"][zone] for m in mine if m["zones"][zone]), None),
                "issued_at": issued_at.isoformat(),
                "covered_until": covered.isoformat(),
                "link": f"{PAGE}?{urlencode({'w': 'hoy', 'l': zone})}",
                "warnings": warnings,
            },
            issued_at,
        )
    return result


def days(payload: dict, now: datetime, stale: bool) -> list[dict]:
    """Today and the next two official days; unknown beyond coverage or when stale."""
    today = local_date(now)
    covered = date.fromisoformat(payload["covered_until"])
    result = []
    for offset in range(DAYS):
        day = today + timedelta(days=offset)
        start = datetime.combine(day, time(), MADRID)
        end = datetime.combine(day + timedelta(days=1), time(), MADRID)
        active = [
            w
            for w in payload["warnings"]
            if datetime.fromisoformat(w["onset"]) < end
            and datetime.fromisoformat(w["expires"]) > max(start, now)
        ]
        if stale or (day > covered and not active):
            level = None
        else:
            level = max((w["level"] for w in active), key=LEVELS.index, default="verde")
        result.append({"date": day.isoformat(), "level": level, "warnings": active})
    return result


def run_aemet_warnings(queue, claim, adapter):
    """AEMET worker job: one two-step download per autonomous community."""
    if not adapter.key:
        raise IngestionError("pending_access", pause=True)
    saved = unchanged = 0
    places = sorted(LOCATIONS.values(), key=lambda p: p.warning_area)
    for area, group in groupby(places, key=lambda p: p.warning_area):
        locations: list[Location] = list(group)
        envelope = adapter.request(BASE + PATH.format(area), authenticated=True)
        if not isinstance(envelope, dict):
            raise IngestionError("invalid_envelope")
        raw = adapter.request(checked_url(envelope.get("datos")), raw=True)
        fetched_at = datetime.now(UTC)
        try:
            payloads = normalize_warnings(raw, locations, fetched_at)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError):
            # Like forecasts: a contract change never pauses observations.
            raise IngestionError("warnings_contract_changed") from None
        with Session(queue.engine) as db, db.begin():
            queue.fence(db, claim)
            for code, (payload, issued_at) in payloads.items():
                if store(db, "aemet", code, "warnings", payload, issued_at, fetched_at):
                    saved += 1
                else:
                    unchanged += 1
    return {"warnings_saved": saved, "warnings_older": unchanged}, {}
