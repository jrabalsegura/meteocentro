from uuid import UUID

from sqlalchemy import exists, select

from meteocentro.models import Exclusion, IdentityExclusion, Provider, Station, StationSource


def permitted_source():
    provider_allowed = StationSource.provider_id.in_(
        select(Provider.id).where(Provider.status.in_(["verified", "paused"]))
    )
    identity_excluded = exists(
        select(IdentityExclusion.id).where(
            IdentityExclusion.provider_id == StationSource.provider_id,
            IdentityExclusion.external_id == StationSource.external_id,
            IdentityExclusion.revoked_at.is_(None),
        )
    )
    return provider_allowed & ~identity_excluded


def eligible_station_ids():
    station_excluded = exists(
        select(Exclusion.id).where(
            Exclusion.station_id == Station.id, Exclusion.revoked_at.is_(None)
        )
    )
    source_excluded = exists(
        select(Exclusion.id).where(
            Exclusion.source_id == StationSource.id, Exclusion.revoked_at.is_(None)
        )
    )
    enabled_source = exists(
        select(StationSource.id).where(
            StationSource.station_id == Station.id,
            StationSource.status == "enabled",
            permitted_source(),
            ~source_excluded,
        )
    )
    return select(Station.id).where(
        Station.moderation_status == "active", ~station_excluded, enabled_source
    )


def eligible_source_ids(station_id: UUID | None = None):
    source_excluded = exists(
        select(Exclusion.id).where(
            Exclusion.source_id == StationSource.id, Exclusion.revoked_at.is_(None)
        )
    )
    query = select(StationSource.id).where(
        StationSource.status == "enabled",
        permitted_source(),
        ~source_excluded,
        StationSource.station_id.in_(eligible_station_ids()),
    )
    return query.where(StationSource.station_id == station_id) if station_id else query
