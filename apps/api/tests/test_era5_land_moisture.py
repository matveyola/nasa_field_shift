from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from field_shift_science.fields.geometry import contains_point
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import (
    VARIABLE,
    ERA5LandMoistureModel,
    MoistureRequestRejected,
    MoistureSourceError,
    field_location,
)

from field_shift.db import build_session_factory
from field_shift.jobs.models import JobKind
from field_shift.jobs.repository import claim, submit, succeed
from field_shift.models import metadata
from field_shift.modules.geo_ml.service import latest_surface_moisture
from field_shift.seed import seed_demo

DAY = date(2026, 8, 1)
FIELD = Polygon(
    type="Polygon",
    coordinates=[
        [
            (-93.45, 41.68),
            (-93.44, 41.68),
            (-93.44, 41.69),
            (-93.45, 41.69),
            (-93.45, 41.68),
        ]
    ],
)


def archive(day: date = DAY) -> dict:
    return {
        "latitude": 41.7,
        "longitude": -93.4,
        "utc_offset_seconds": 0,
        "hourly_units": {VARIABLE: "m³/m³"},
        "hourly": {
            "time": [f"{day}T{hour:02}:00" for hour in range(24)],
            VARIABLE: [0.2] * 12 + [0.4] * 12,
        },
    }


def test_sampling_point_is_inside_a_concave_field():
    ring = [(0, 0), (4, 0), (4, 4), (3, 4), (3, 1), (1, 1), (1, 4), (0, 4), (0, 0)]
    polygon = Polygon(type="Polygon", coordinates=[ring])
    latitude, longitude = field_location(polygon)
    assert contains_point(polygon.coordinates[0], longitude, latitude)


def test_live_archive_contract_preserves_native_grid_and_daily_mean(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)

    def fetch(url: str) -> dict:
        request = urlsplit(url)
        query = parse_qs(request.query)
        assert request.hostname == "archive-api.open-meteo.com"
        assert query["models"] == ["era5_land"]
        assert query["start_date"] == query["end_date"] == [str(DAY)]
        assert query["elevation"] == ["nan"]
        assert query["cell_selection"] == ["nearest"]
        return archive()

    result = ERA5LandMoistureModel(fetch).run("F-03", 2026, FIELD, DAY)

    assert not result.demo
    assert result.depth_cm == (0, 7)
    assert result.minimum == result.maximum == 0.3
    assert len(result.cells) == 1
    ring = result.cells[0].geometry.coordinates[0]
    assert max(lon for lon, _ in ring) - min(lon for lon, _ in ring) == pytest.approx(0.1)
    assert max(lat for _, lat in ring) - min(lat for _, lat in ring) == pytest.approx(0.1)
    assert result.source is not None
    assert result.source.sample_count == 24
    assert result.source.grid_latitude == 41.7
    assert result.source.requested_longitude == -93.445


@pytest.mark.parametrize("broken", ["missing", "wrong date", "wrong grid", "bad units"])
def test_invalid_or_partial_source_never_creates_regional_sample(broken: str) -> None:
    data = archive()
    if broken == "missing":
        data["hourly"][VARIABLE][9] = None
    elif broken == "wrong date":
        data["hourly"]["time"][0] = "2026-07-31T23:00"
    elif broken == "wrong grid":
        data["longitude"] = -94.0
    else:
        data["hourly_units"][VARIABLE] = "mm"

    with pytest.raises(MoistureSourceError, match="complete 24-hour"):
        ERA5LandMoistureModel(lambda _: data).run("F-03", 2026, FIELD, DAY)


def test_latest_recovers_when_archive_rejects_an_unpublished_day():
    requests = []

    def fetch(url):
        query = parse_qs(urlsplit(url).query)
        requests.append((query["start_date"][0], query["end_date"][0]))
        if query["start_date"] != query["end_date"]:
            raise MoistureRequestRejected("Range end is unavailable")
        return archive()

    result = ERA5LandMoistureModel(fetch).run_latest("F-03", 2026, FIELD, DAY + timedelta(days=6))

    assert result.date == DAY
    assert result.source.sample_count == 24
    assert requests == [(str(DAY), str(DAY + timedelta(days=1))), (str(DAY), str(DAY))]


def test_latest_for_an_explicit_past_season_stays_in_that_season():
    last_day = date(2026, 12, 31)

    def fetch(url):
        query = parse_qs(urlsplit(url).query)
        assert query["start_date"] == query["end_date"] == [str(last_day)]
        return archive(last_day)

    result = ERA5LandMoistureModel(fetch).run_latest("F-03", 2026, FIELD, date(2027, 1, 6))
    assert (result.date, result.season) == (last_day, 2026)


def test_saved_historical_sample_is_loaded_only_for_its_field_and_season() -> None:
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
    historical = ERA5LandMoistureModel(lambda _: archive()).run("F-03", 2026, FIELD, DAY)
    assert historical.source is not None
    now = datetime(2026, 11, 9, tzinfo=UTC)
    with factory.begin() as session:
        assert latest_surface_moisture(session, "F-03", 2026).demo
        pending = submit(
            session,
            kind=JobKind.GEO_ML,
            field_id="F-03",
            inputs={"season": 2026, "source": "historical"},
            now=now,
        )
        assert latest_surface_moisture(session, "F-03", 2026).demo
        lease = claim(session, pending.id, lease_seconds=60, now=now)
        assert lease is not None
        assert succeed(session, lease, historical.model_dump(mode="json"), now=now)

        assert latest_surface_moisture(session, "F-03", 2026) == historical
        assert latest_surface_moisture(session, "F-03", 2025).demo
        assert latest_surface_moisture(session, "F-02", 2026).demo
