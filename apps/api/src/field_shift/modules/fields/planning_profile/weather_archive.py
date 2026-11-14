"""Immutable, farm-scoped issue snapshots. Grid IDs remain provider-owned."""

import json

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.contracts.planning_v1 import WeatherScenarioSet
from field_shift.db import Base

from .service import ProfileError, canonical_hash


class WeatherIssue(Base):
    __tablename__ = "planning_weather_issues"
    __table_args__ = (UniqueConstraint("farm_id", "scenario_set_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"))
    scenario_set_id: Mapped[str] = mapped_column(String(128))
    checksum: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON)


def preserve_issue(session, farm_id, weather):
    fingerprint = canonical_hash(weather)
    row = session.scalar(
        select(WeatherIssue).where(
            WeatherIssue.farm_id == farm_id,
            WeatherIssue.scenario_set_id == weather.scenario_set_id,
        )
    )
    if row:
        if row.checksum != fingerprint:
            raise ProfileError("conflict", "Weather issue ID reused with changed data")
        return WeatherScenarioSet.model_validate_json(json.dumps(row.document))
    session.add(
        WeatherIssue(
            farm_id=farm_id,
            scenario_set_id=weather.scenario_set_id,
            checksum=fingerprint,
            document=weather.model_dump(mode="json"),
        )
    )
    session.flush()
    return weather
