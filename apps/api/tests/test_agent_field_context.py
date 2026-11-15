import json
from datetime import timedelta

import pytest
from field_shift_science.fields.schemas import FieldRead
from field_shift_science.geo_ml.schemas import GeoResult, MoistureSource
from field_shift_science.simulation.schemas import HistoricalProvenance, WaterResult
from field_shift_science.weather.scenarios import Scenario
from sqlalchemy import func, select
from test_agent_evidence import (
    NOW,
    TODAY,
    database,  # noqa: F401
    forecast,
    vegetation,
)

from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.agent import service
from field_shift.modules.agent.field_context import build_field_context
from field_shift.modules.fields.service import require_field
from field_shift.modules.terrain.schemas import TerrainResult
from field_shift.modules.terrain.service import MODEL_VERSION as TERRAIN_MODEL


@pytest.fixture
def field() -> FieldRead:
    # The concave field's centroid differs from its vertex average.
    return FieldRead(
        id="F-03",
        name="South Field",
        crop="Maize",
        area_ha=10,
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    (31, 50),
                    (31.03, 50),
                    (31.03, 50.01),
                    (31.01, 50.01),
                    (31.01, 50.03),
                    (31, 50.03),
                    (31, 50),
                ],
            ],
        },
        boundary_revision=0,
    )


def build(field: FieldRead, **evidence) -> dict:
    # The exact signed request must be JSON; reject non-finite JSON numbers.
    return json.loads(
        json.dumps(
            build_field_context(field, season=2026, locale="uk", now=NOW, **evidence),
            allow_nan=False,
        )
    )


def test_context_projects_current_modelled_water_and_satellite_facts(field):
    context = build(field, forecast=forecast(), vegetation=vegetation(), revision=42)
    assert context["field_id"] == "F-03"
    assert context["revision"] == 42
    assert context["centroid"] == pytest.approx([31.011, 50.011])
    assert context["area_ha"] != field.area_ha
    assert context["region"] == {"country_iso2": "ZZ"}
    assert context["locale"] == "uk"

    facts = {fact["id"]: fact for fact in context["facts"]}
    reserve = facts["f_forecast_min_root_zone_reserve"]
    assert reserve["quantity"] == {"value": 60, "unit": "mm"}
    assert reserve["provenance"]["kind"] == "deterministic_calc"
    assert reserve["provenance"]["data_status"] == "cached"
    assert reserve["provenance"]["source"]["product_id"] == "FAO-56"
    assert "ECMWF IFS HRES forecast" in reserve["provenance"]["method"]
    assert reserve["provenance"]["temporal"] == {"start": "2026-11-10", "end": "2026-11-12"}
    assert facts["f_water_stress_days"]["quantity"] == {"value": 1, "unit": "d"}

    ndvi = facts["f_ndvi_latest"]
    assert ndvi["quantity"] == {"value": 0.61, "unit": "1"}
    assert ndvi["provenance"]["source"]["product_id"] == "Sentinel-2 L2A"
    assert ndvi["provenance"]["temporal"] == {"start": "2026-11-07", "end": "2026-11-07"}
    assert facts["f_ndvi_clear_coverage"]["quantity"] == {"value": 88, "unit": "%"}
    assert facts["f_ndvi_low_area_share"]["quantity"] == {"value": 18, "unit": "%"}
    assert [layer["layer_id"] for layer in context["available_layers"]] == ["l_ndvi_variability"]
    assert context["available_charts"] == [
        {"chart_id": "c_water_forecast", "metric_ids": ["m_rootzone_reserve", "m_precip"]},
        {"chart_id": "c_vegetation", "metric_ids": ["m_ndvi"]},
    ]
    assert {series["metric_id"] for series in context["timeseries_index"]} == {
        "m_rootzone_reserve",
        "m_precip",
        "m_ndvi",
    }


@pytest.mark.parametrize(
    "update",
    [
        {"field_id": "F-02"},
        {"season": 2025},
        {"initialized_at": NOW - timedelta(days=4)},
        {"initialized_at": NOW + timedelta(hours=1)},
        {"generated_at": NOW + timedelta(hours=1)},
        {"threshold_mm": float("nan")},
        {"days": forecast().days[1:]},
        {"days": [forecast().days[0], forecast().days[2]]},
    ],
)
def test_bad_or_expired_forecast_advertises_no_water_fact_or_chart(field, update):
    context = build(field, forecast=forecast().model_copy(update=update))
    assert context["facts"] == context["timeseries_index"] == context["available_charts"] == []


@pytest.mark.parametrize(
    "result",
    [
        vegetation(day=TODAY + timedelta(days=1)),
        vegetation().model_copy(update={"boundary_revision": 1}),
        vegetation().model_copy(update={"status": "unavailable"}),
        vegetation().model_copy(update={"observations": []}),
        vegetation().model_copy(
            update={
                "observations": [
                    vegetation().observations[0].model_copy(update={"valid_percent": 20})
                ]
            }
        ),
    ],
)
def test_future_wrong_boundary_and_clouded_ndvi_advertise_no_actions(field, result):
    context = build(field, vegetation=result)
    assert context["facts"] == context["available_layers"] == context["available_charts"] == []


def test_older_usable_ndvi_is_labelled_by_age_and_wrong_scene_layer_is_omitted(field):
    saved = vegetation(day=TODAY - timedelta(days=50))
    saved.heterogeneity = vegetation().heterogeneity
    context = build(field, vegetation=saved)
    assert "50 days old" in context["facts"][0]["provenance"]["method"]
    assert [chart["chart_id"] for chart in context["available_charts"]] == ["c_vegetation"]
    assert context["available_layers"] == []


def historical_run() -> WaterResult:
    days = [
        day.model_copy(update={"date": day.date - timedelta(days=10), "rain_mm": 2.0})
        for day in forecast().days
    ]
    return WaterResult(
        field_id="F-03",
        season=2026,
        scenario=Scenario.LESS_RAIN,
        demo=False,
        source="historical",
        provenance=HistoricalProvenance(
            weather_source="Open-Meteo",
            weather_model="ERA5",
            weather_url="https://www.ecmwf.int",
            latitude=50,
            longitude=31,
            grid_latitude=50,
            grid_longitude=31,
            grid_resolution_degrees=0.25,
            timezone="UTC",
            start_date=days[0].date,
            end_date=days[-1].date,
            rainfall_multiplier=0.7,
            initial_reserve_mm=90,
            initial_reserve_source="user_input",
            initial_moisture_date=None,
            initial_theta_m3_m3=None,
            capacity_mm=150,
            root_depth_m=1,
            field_capacity=0.3,
            wilting_point=0.15,
            crop_coefficient=1,
            depletion_fraction=0.5,
            model_version="fao56-test",
            limitations=[],
            sources=[],
        ),
        threshold_mm=65,
        flowering_start=None,
        flowering_end=None,
        first_stress_date=days[-1].date,
        min_reserve_mm=60,
        stress_days=1,
        days=days,
    )


def test_selected_historical_run_preserves_real_scenario_and_does_not_leak_forecast(field):
    result = historical_run()
    context = build(field, water=result, scenario=Scenario.LESS_RAIN, forecast=forecast())
    assert [chart["chart_id"] for chart in context["available_charts"]] == ["c_water_season"]
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert facts["f_historical_precipitation_total"]["quantity"] == {"value": 6, "unit": "mm"}
    provenance = facts["f_historical_min_root_zone_reserve"]["provenance"]
    assert "historical simulation for scenario less_rain" in provenance["method"]
    assert "rainfall multiplier 0.7" in provenance["method"]
    assert "ui_state" not in context


def test_demo_or_wrong_scenario_results_do_not_become_cached_facts(field):
    result = historical_run()
    for context in (
        build(field, water=result.model_copy(update={"demo": True})),
        build(field, water=result, scenario=Scenario.DRY_YEAR),
    ):
        assert context["facts"] == context["available_charts"] == []


def test_unknown_scenario_never_attaches_a_historical_run(field):
    context = build(field, water=historical_run())
    assert context["facts"] == context["available_charts"] == []


def test_dry_year_run_keeps_its_own_name_and_is_not_a_named_drought(field):
    result = historical_run().model_copy(update={"scenario": Scenario.DRY_YEAR})
    context = build(field, water=result, scenario=Scenario.DRY_YEAR)
    method = context["facts"][0]["provenance"]["method"]
    assert "scenario dry_year" in method
    assert "drought" not in json.dumps(context)


def era5(**update) -> GeoResult:
    result = GeoResult(
        field_id="F-03",
        season=2026,
        demo=False,
        date=TODAY - timedelta(days=7),
        depth_cm=(0, 7),
        unit="m³/m³",
        minimum=0.2846,
        maximum=0.2846,
        cells=[],
        source=MoistureSource(
            name="ERA5-Land",
            provider="Copernicus Climate Change Service via Open-Meteo",
            url="https://doi.org/10.24381/cds.e2161bac",
            kind="reanalysis",
            resolution_degrees=0.1,
            grid_latitude=50.0,
            grid_longitude=31.0,
            sample_count=24,
            requested_latitude=50.01,
            requested_longitude=31.01,
        ),
    )
    return result.model_copy(update=update)


def terrain(**update) -> TerrainResult:
    result = TerrainResult(
        field_id="F-03",
        boundary_revision=0,
        generated_at=NOW - timedelta(days=30),
        status="ready",
        model_version=TERRAIN_MODEL,
        elevation_min_m=141.37,
        elevation_max_m=152.81,
        coverage_percent=98.5,
        minimum_display_slope_percent=1,
        limitations=[],
        attribution="Copernicus",
    )
    return result.model_copy(update=update)


def test_saved_surface_moisture_and_terrain_are_separate_traceable_facts(field):
    context = build(field, surface_moisture=era5(), terrain=terrain())
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert set(facts) == {"f_surface_soil_moisture", "f_elevation_min", "f_elevation_max"}
    # Surface moisture is never relabelled as root-zone water, and has no chart of its own.
    assert context["available_charts"] == context["timeseries_index"] == []

    moisture = facts["f_surface_soil_moisture"]
    assert moisture["domain"] == "soil"
    assert moisture["quantity"] == {"value": 0.285, "unit": "m3/m3"}
    assert moisture["depth_cm"] == [0, 7]
    provenance = moisture["provenance"]
    # A weather-model reanalysis, not satellite data: the widget labels "satellite_product" as
    # "Satellite data" (changed on purpose 2026-11-14; the contract files reanalysis under
    # weather_data).
    assert provenance["kind"] == "weather_data"
    assert provenance["data_status"] == "cached"
    assert provenance["source"] == {
        "provider": "Copernicus Climate Change Service via Open-Meteo",
        "product_id": "ERA5-Land soil_moisture_0_to_7cm",
        "version": "unverified",
        "url": "https://doi.org/10.24381/cds.e2161bac",
    }
    assert provenance["temporal"] == {
        "start": "2026-11-03",
        "end": "2026-11-03",
        "aggregation": "daily_mean",
    }
    assert provenance["spatial"] == {"resolution_m": pytest.approx(11_100)}
    assert "7 days old" in provenance["method"]
    assert "not root-zone water" in provenance["method"]
    # The farmer reads that it is a coarse model estimate, not a satellite or a probe in the field.
    method = provenance["method"]
    assert "weather-model estimate (reanalysis), not a satellite or on-field measurement" in method
    assert "0.1 degree grid cell (about 11 km)" in method
    assert method.endswith("not root-zone water.")  # under the 300-character cap, not cut
    assert provenance["retrieved_at"] == NOW.isoformat()

    low, high = facts["f_elevation_min"], facts["f_elevation_max"]
    assert (low["domain"], low["quantity"]) == ("terrain", {"value": 141, "unit": "m"})
    assert high["quantity"] == {"value": 153, "unit": "m"}
    terrain_provenance = high["provenance"]
    assert terrain_provenance["kind"] == "deterministic_calc"
    assert terrain_provenance["source"]["product_id"] == "Copernicus DEM GLO-30"
    assert terrain_provenance["source"]["version"].startswith("2021 AWS release")
    assert terrain_provenance["spatial"] == {"resolution_m": 30}
    assert terrain_provenance["retrieved_at"] == (NOW - timedelta(days=30)).isoformat()
    # A static elevation model has no observation window to claim.
    assert "temporal" not in terrain_provenance
    assert "includes vegetation and buildings" in terrain_provenance["method"]


def test_old_surface_moisture_is_labelled_by_age_not_presented_as_current(field):
    old = era5(date=TODAY - timedelta(days=200))
    provenance = build(field, surface_moisture=old)["facts"][0]["provenance"]
    assert "200 days old" in provenance["method"]
    assert provenance["temporal"]["end"] == "2026-04-24"
    assert provenance["data_status"] == "cached"


@pytest.mark.parametrize(
    "result",
    [
        era5(field_id="F-02"),
        era5(season=2025),
        era5(date=TODAY.replace(year=2025)),
        era5(demo=True),
        era5(date=TODAY + timedelta(days=1)),
        era5(depth_cm=(5, 5)),
        era5(source=None),
        era5(source=era5().source.model_copy(update={"kind": "ml", "name": "Field Shift"})),
        era5(maximum=0.31),
        era5(minimum=1.2, maximum=1.2),
    ],
)
def test_unverifiable_surface_moisture_advertises_no_fact(field, result):
    assert build(field, surface_moisture=result)["facts"] == []


@pytest.mark.parametrize(
    "result",
    [
        terrain(field_id="F-02"),
        terrain(boundary_revision=1),
        terrain(status="unavailable"),
        terrain(coverage_percent=80),
        terrain(elevation_min_m=None),
        terrain(elevation_min_m=160.0),
    ],
)
def test_terrain_for_another_boundary_or_incomplete_coverage_advertises_no_fact(field, result):
    assert build(field, terrain=result)["facts"] == []


def test_terrain_with_an_implausible_timestamp_reports_the_read_time(field):
    future = terrain(generated_at=NOW + timedelta(hours=1))
    assert build(field, terrain=future)["facts"][0]["provenance"]["retrieved_at"] == NOW.isoformat()


def test_new_evidence_leaves_forecast_and_vegetation_facts_unchanged(field):
    before = build(field, forecast=forecast(), vegetation=vegetation())
    after = build(
        field,
        forecast=forecast(),
        vegetation=vegetation(),
        surface_moisture=era5(),
        terrain=terrain(),
    )
    assert after["facts"][: len(before["facts"])] == before["facts"]
    for key in ("timeseries_index", "available_charts", "available_layers"):
        assert after[key] == before[key]


def _save(session, *, analysis: str | None, result: dict) -> None:
    inputs = {"season": 2026, "scenario": "baseline", "source": "historical"}
    if analysis is not None:
        inputs["analysis"] = analysis
    job = repository.submit(
        session, kind=JobKind.GEO_ML, field_id="F-03", boundary_revision=0, inputs=inputs, now=NOW
    )
    job.status = JobStatus.SUCCEEDED
    job.result = result
    session.commit()


def _ids(session, season: int) -> set[str]:
    context = service.assistant_field_context(session, "F-03", season, Scenario.DRY_YEAR, "en")
    return {fact["id"] for fact in context["facts"]}


def test_widget_context_reads_only_stored_results_for_the_current_boundary(database):  # noqa: F811
    added = {"f_surface_soil_moisture", "f_elevation_min", "f_elevation_max"}
    with database() as session:
        jobs = session.scalar(select(func.count(Job.id)))
        assert not added & _ids(session, 2026)
        # A missing record is not computed or queued to fill the context.
        assert session.scalar(select(func.count(Job.id))) == jobs

        _save(session, analysis=None, result=era5().model_dump(mode="json"))
        _save(session, analysis="terrain", result=terrain().model_dump(mode="json"))
        assert added <= _ids(session, 2026)
        # Another season has no saved ERA5-Land record; the static terrain still applies.
        assert added & _ids(session, 2025) == {"f_elevation_min", "f_elevation_max"}

        require_field(session, "F-03").boundary_revision = 1
        session.commit()
        assert not added & _ids(session, 2026)
