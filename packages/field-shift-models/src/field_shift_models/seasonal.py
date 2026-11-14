"""Coherent historical weather trajectories for future crop-planning experiments.

An analogue member is an observed past sequence mapped to future dates. It is
not a daily weather prediction or a calibrated event probability. All fields
share each member's source years, preserving observed spatial co-variation.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from field_shift_contracts.planning_v1.common import DateWindow, SourceRecord
from field_shift_contracts.planning_v1.weather import (
    DailyWeather,
    WeatherScenario,
    WeatherScenarioSet,
    WeatherTrace,
)

from .provenance import digest


@dataclass(frozen=True)
class HistoricalArchive:
    field_snapshot_id: str
    grid_id: str
    source_id: str
    native_resolution_m: float
    representative_latitude: float
    representative_longitude: float
    days: tuple[DailyWeather, ...]


def historical_scenarios(
    *,
    archives: Mapping[str, HistoricalArchive],
    source: SourceRecord,
    source_years: list[int],
    start: date,
    end: date,
    as_of: datetime,
) -> WeatherScenarioSet:
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("Explicit UTC issue time required")
    if start <= as_of.date() or end < start or (end - start).days > 1095:
        raise ValueError("Future window must be ordered and bounded to three years")
    if not archives or len(archives) > 30 or not 1 <= len(source_years) <= 8:
        raise ValueError("One to thirty fields and one to eight scenario members required")
    if len(set(source_years)) != len(source_years):
        raise ValueError("Duplicate analogue member years")
    if source.kind not in {"measured", "modelled"} or source.quality != "valid":
        raise ValueError("Valid historical observations or reanalysis provenance required")
    if source.available_at > as_of or not source.checksum_sha256:
        raise ValueError("Pinned historical source must be available at issue time")
    length = (end - start).days + 1
    members = []
    for year in source_years:
        try:
            original_start = date(year, start.month, start.day)
        except ValueError as exc:
            raise ValueError("Analogue year cannot represent the requested start date") from exc
        original_end = original_start + timedelta(days=length - 1)
        if original_end >= as_of.date():
            raise ValueError("Every historical source day must precede issue time")
        traces = []
        for field_id, archive in sorted(archives.items()):
            if archive.field_snapshot_id != field_id or archive.source_id != source.id:
                raise ValueError("Archive scope or source mismatch")
            if not 1 <= len(archive.days) <= 14640:
                raise ValueError("Historical archive must be bounded to forty years")
            by_date = {d.date: d for d in archive.days}
            if len(by_date) != len(archive.days):
                raise ValueError("Duplicate source weather dates")
            days = []
            for offset in range(length):
                original = original_start + timedelta(days=offset)
                point = by_date.get(original)
                if point is None or point.quality != "valid":
                    raise ValueError("Incomplete historical member; no gap filling permitted")
                if any(
                    getattr(point, k) is None
                    for k in ("tmin_c", "tmax_c", "liquid_rain_mm", "et0_mm")
                ):
                    raise ValueError("Missing daily forcing cannot become a complete scenario")
                days.append(
                    DailyWeather.model_validate(
                        point.model_dump()
                        | {
                            "date": start + timedelta(days=offset),
                            "source_date": original,
                            "lead_days": None,
                        }
                    )
                )
            traces.append(
                WeatherTrace(
                    field_snapshot_id=field_id,
                    grid_id=archive.grid_id,
                    source_id=archive.source_id,
                    native_resolution_m=archive.native_resolution_m,
                    representative_latitude=archive.representative_latitude,
                    representative_longitude=archive.representative_longitude,
                    days=days,
                )
            )
        members.append(
            WeatherScenario(
                trajectory_id=f"analogue-{year}", member_id=str(year), weight=None, traces=traces
            )
        )
    identity = digest(
        {
            "source": source.model_dump(mode="json"),
            "members": [m.model_dump(mode="json") for m in members],
            "as_of": as_of.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    )
    return WeatherScenarioSet(
        schema_version="1.0.0",
        scenario_set_id="analogue-" + identity[:32],
        as_of=as_of,
        issued_at=as_of,
        cycle_at=as_of,
        model_version="historical-analogue-v1",
        valid_window=DateWindow(start=start, end=end),
        scenario_type="historical_replay",
        stale_after=as_of + timedelta(days=1),
        weight_calibration=None,
        sources=[source],
        scenarios=members,
    )
