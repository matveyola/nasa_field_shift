import json
from datetime import timedelta

import pytest
from field_shift_science.fields.schemas import FieldRead
from field_shift_science.simulation.schemas import HistoricalProvenance, WaterResult
from field_shift_science.weather.scenarios import Scenario
from test_agent_evidence import NOW, TODAY, forecast, vegetation

from field_shift.modules.agent.field_context import build_field_context


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
