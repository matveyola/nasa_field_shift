"""Global calendars and continuous water tests prove mechanics, not empirical accuracy."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from field_shift_science.layered_water.planting import CalendarParameters, Planting
from field_shift_science.layered_water.runner import LayeredParameters, run_water_balance
from field_shift_science.layered_water.weather import WeatherDay


def parameters(**overrides):
    values = dict(
        field_capacity=(0.30,) * 15,
        wilting_point=(0.15,) * 15,
        saturation=(0.45,) * 15,
        initial_vwc=(0.25,) * 15,
        drainage_fraction=(0.1,) * 15,
        curve_numbers=dict(row=78.0, small_grain=75.0, residue=83.0, bare=86.0),
        snow_temperature_c=0.0,
        melt_mm_per_c_day=3.0,
        freeze_tmean_7d_c=-1.0,
        frozen_curve_number=95.0,
        initial_residue_cover=0.3,
        initial_snow_mm=0.0,
        prior_tmean_c=(15.0,) * 7,
        source="Explicit synthetic physical invariant parameters; not regional validation",
    )
    return LayeredParameters(**(values | overrides))


def calendar(start=date(2027, 4, 1), crop="Maize", dormancy=(), active=120):
    end = start + timedelta(days=active - 1 + sum((b - a).days + 1 for a, b in dormancy))
    return CalendarParameters(
        crop,
        start,
        end,
        (20, 30, 40, 30),
        0.3,
        1.2,
        0.35,
        0.15,
        1.0,
        0.55,
        2.0,
        0.3,
        "row",
        dormancy,
        "Explicit synthetic calendar; no cultivar or geographic validation",
    )


def forcing(start, end, cold=False, rain=2.0):
    return [
        WeatherDay(
            start + timedelta(days=i),
            -2.0 if cold else 25.0,
            -8.0 if cold else 15.0,
            rain,
            1.0 if cold else 4.0,
            source="synthetic-invariant-v1",
        )
        for i in range((end - start).days + 1)
    ]


def test_north_and_south_use_explicit_local_calendars_not_iowa_months():
    north = calendar()
    south = calendar(date(2026, 10, 1))
    for c in (north, south):
        run = run_water_balance(
            parameters(), forcing(c.sowing, c.harvest), [c], land_status="arable"
        )
        assert run.days[0].date == c.sowing
        assert run.days[-1].date == c.harvest
        assert run.max_balance_error_mm < 1e-6
        assert run.status == "experimental_water_only"
    assert south.harvest.year == 2027 and south.sowing.year == 2026


def test_mixed_crops_keep_water_and_snow_continuously_through_winter():
    first = calendar(date(2026, 4, 1), "Maize")
    winter = calendar(date(2026, 10, 1), "Wheat", ((date(2026, 12, 1), date(2027, 3, 31)),))
    wx = forcing(first.sowing, winter.harvest)
    wx = [
        replace(d, tmax=-3.0, tmin=-7.0, et0_mm=0.0)
        if date(2026, 12, 1) <= d.date <= date(2027, 2, 28)
        else d
        for d in wx
    ]
    run = run_water_balance(parameters(), wx, [first, winter], land_status="arable")
    assert any(d.snow_mm > 0 and d.frozen for d in run.days)
    assert any(d.melt_mm > 0 for d in run.days)
    assert {d.cover for d in run.days} == {"Maize", "Wheat", "fallow"}
    assert run.max_balance_error_mm < 1e-6
    # The next crop opens on the actual preceding profile, never a preset 50% reset.
    by_date = {d.date: d for d in run.days}
    assert by_date[winter.sowing].profile_mm != pytest.approx(run.initial_profile_mm)
    assert all(v >= 0 for d in run.days for v in (d.snow_mm, d.profile_mm, d.eta_mm, d.drainage_mm))


def test_zero_flux_preserves_nonuniform_opening_water_exactly():
    p = parameters(initial_vwc=tuple(0.16 + i * 0.002 for i in range(15)))
    wx = forcing(date(2027, 1, 1), date(2027, 1, 3), rain=0.0)
    wx = [replace(w, et0_mm=0.0) for w in wx]
    run = run_water_balance(p, wx, [], land_status="arable")
    assert run.closing_vwc == pytest.approx(p.initial_vwc)


@pytest.mark.parametrize("land", ["ice", "ocean", "non_arable", "unknown"])
def test_antarctic_ice_and_other_non_arable_land_abstain(land):
    with pytest.raises(ValueError, match="arable"):
        run_water_balance(
            parameters(), forcing(date(2027, 1, 1), date(2027, 1, 3)), [], land_status=land
        )


def test_rice_does_not_silently_use_a_dryland_model():
    c = calendar(crop="Rice")
    with pytest.raises(ValueError, match="Paddy"):
        run_water_balance(parameters(), forcing(c.sowing, c.harvest), [c], land_status="arable")


@pytest.mark.parametrize(
    "change",
    [
        lambda wx: wx[1:],
        lambda wx: [wx[0], wx[2], *wx[3:]],
        lambda wx: [wx[0], wx[0], *wx[2:]],
        lambda wx: [replace(wx[0], rain_mm=float("nan")), *wx[1:]],
        lambda wx: [replace(wx[0], gap=True), *wx[1:]],
    ],
)
def test_incomplete_or_invalid_daily_inputs_are_never_filled(change):
    c = calendar()
    wx = change(forcing(c.sowing, c.harvest))
    with pytest.raises(ValueError):
        run_water_balance(parameters(), wx, [c], land_status="arable")


def test_roots_and_overlapping_calendars_cannot_be_silently_clipped():
    c = calendar()
    with pytest.raises(ValueError, match="root"):
        run_water_balance(
            parameters(),
            forcing(c.sowing, c.harvest),
            [replace(c, root_max_m=2.0)],
            land_status="arable",
        )
    with pytest.raises(ValueError, match="Overlapping"):
        run_water_balance(parameters(), forcing(c.sowing, c.harvest), [c, c], land_status="arable")


def test_dormancy_and_active_phase_boundary_counts_are_exact():
    c = calendar(date(2026, 10, 1), dormancy=((date(2026, 12, 1), date(2027, 3, 31)),))
    p = Planting(c)
    assert p.stage(date(2026, 12, 1)) == "dormant"
    assert p.stage(date(2027, 3, 31)) == "dormant"
    assert p.stage(c.sowing) == "ini" and p.stage(c.harvest) == "late"
    with pytest.raises(ValueError, match="active calendar"):
        replace(c, stage_lengths=(20, 30, 40, 31))


@pytest.mark.parametrize(
    "irrigation",
    [{date(2027, 4, 1): -1.0}, {date(2025, 1, 1): 1.0}, {date(2027, 4, 1): float("inf")}],
)
def test_bad_irrigation_fails_before_simulation(irrigation):
    c = calendar()
    with pytest.raises(ValueError, match="Irrigation"):
        run_water_balance(
            parameters(),
            forcing(c.sowing, c.harvest),
            [c],
            land_status="arable",
            irrigation=irrigation,
        )


def archive_inputs():
    from field_shift_models.fixtures import synthetic_inputs
    from field_shift_models.seasonal import HistoricalArchive

    _, _, weather, _ = synthetic_inputs()
    trace = weather.scenarios[0].traces[0]
    source = weather.sources[0].model_copy(update={"kind": "modelled"})
    records = []
    start = date(2020, 1, 1)
    point = trace.days[0]
    for i in range(366 + 365):
        d = start + timedelta(days=i)
        records.append(
            point.model_copy(
                update={
                    "date": d,
                    "source_date": d,
                    "lead_days": None,
                    "tmin_c": float(i % 11),
                    "tmax_c": float(i % 11 + 10),
                }
            )
        )
    common = dict(
        grid_id="test-grid",
        source_id=source.id,
        native_resolution_m=9000.0,
        representative_latitude=50.45,
        representative_longitude=30.52,
    )
    a = HistoricalArchive(field_snapshot_id="a", days=tuple(records), **common)
    b = HistoricalArchive(
        field_snapshot_id="b",
        days=tuple(
            p.model_copy(update={"tmin_c": p.tmin_c + 1, "tmax_c": p.tmax_c + 1}) for p in records
        ),
        **common,
    )
    return {"a": a, "b": b}, source


def test_future_members_preserve_shared_year_and_daily_spatial_covariation():
    from field_shift_models.seasonal import historical_scenarios

    archives, source = archive_inputs()
    result = historical_scenarios(
        archives=archives,
        source=source,
        source_years=[2020, 2021],
        start=date(2028, 4, 1),
        end=date(2028, 9, 30),
        as_of=datetime(2027, 4, 1, tzinfo=UTC),
    )
    assert result.scenario_type == "historical_replay" and result.weight_calibration is None
    assert all(m.weight is None for m in result.scenarios)
    for member in result.scenarios:
        a, b = member.traces
        for x, y in zip(a.days, b.days, strict=True):
            assert x.source_date == y.source_date
            assert y.tmin_c - x.tmin_c == pytest.approx(1.0)
            assert x.date.year == 2028 and x.lead_days is None
            assert x.source_date.year == int(member.member_id)


def test_future_weather_cannot_enter_analogue_baseline():
    from field_shift_models.seasonal import historical_scenarios

    archives, source = archive_inputs()
    with pytest.raises(ValueError, match="precede issue"):
        historical_scenarios(
            archives=archives,
            source=source,
            source_years=[2026],
            start=date(2027, 4, 1),
            end=date(2028, 4, 1),
            as_of=datetime(2027, 1, 1, tzinfo=UTC),
        )


def test_leap_day_must_have_a_real_corresponding_source_day():
    from field_shift_models.seasonal import historical_scenarios

    archives, source = archive_inputs()
    with pytest.raises(ValueError, match="start date"):
        historical_scenarios(
            archives=archives,
            source=source,
            source_years=[2021],
            start=date(2028, 2, 29),
            end=date(2028, 4, 1),
            as_of=datetime(2027, 4, 1, tzinfo=UTC),
        )


def test_three_year_seasonal_plan_keeps_every_source_day_without_annual_reset():
    from field_shift_models.seasonal import HistoricalArchive, historical_scenarios

    archives, source = archive_inputs()
    a = archives["a"]
    point = a.days[0]
    records = tuple(
        point.model_copy(update={"date": date(2017, 1, 1) + timedelta(days=i)})
        for i in range(4 * 366)
    )
    a = HistoricalArchive(**(vars(a) | {"days": records}))
    result = historical_scenarios(
        archives={"a": a},
        source=source,
        source_years=[2017],
        start=date(2028, 4, 1),
        end=date(2031, 3, 31),
        as_of=datetime(2027, 4, 1, tzinfo=UTC),
    )
    rows = result.scenarios[0].traces[0].days
    assert len(rows) == 1095
    assert all((b.source_date - a.source_date).days == 1 for a, b in zip(rows, rows[1:]))


def experiment_document():
    from dataclasses import asdict

    c = calendar(date(2028, 4, 1))
    wx = forcing(c.sowing, c.harvest)
    return {
        "as_of": "2027-04-01T00:00:00Z",
        "location": {"latitude": 50.45, "longitude": 30.52},
        "parameter_region": {
            "region_id": "explicit-synthetic",
            "bounds": [20.0, 40.0, 40.0, 60.0],
            "source": "synthetic invariant only",
            "validated": False,
        },
        "parameters": asdict(parameters()),
        "land_status": "arable",
        "alternatives": [
            {"id": "maize", "calendars": [asdict(c)], "irrigation_mm": {}},
            {"id": "rice", "calendars": [asdict(replace(c, crop_id="Rice"))], "irrigation_mm": {}},
        ],
        "scenarios": [
            {
                "id": "reference",
                "kind": "synthetic",
                "available_at": "2027-03-31T00:00:00Z",
                "weather": [asdict(w) for w in wx],
            }
        ],
    }


def json_document():
    import json

    from field_shift_models.provenance import _json

    return json.loads(json.dumps(experiment_document(), default=_json))


def test_comparison_runs_real_water_keeps_unsupported_rice_and_never_invents_yield():
    from field_shift_models.global_water import compare

    result = compare(json_document())
    assert result["ranking_by_worst_tested_unmet_fraction"] == ["maize"]
    maize, rice = result["alternatives"]
    assert maize["members"][0]["max_balance_error_mm"] < 1e-6
    assert rice["status"] == "unsupported"
    assert all(row["yield_t_ha"] is None and row["gross_margin"] is None for row in (maize, rice))
    assert result["promotion"] is result["validated"] is False


def test_regional_parameters_do_not_silently_transfer_to_rio():
    from field_shift_models.global_water import compare

    document = json_document()
    document["location"] = {"latitude": -22.91, "longitude": -43.17}
    with pytest.raises(ValueError, match="footprint"):
        compare(document)
    document["parameter_region"]["bounds"] = [-50.0, -30.0, -40.0, -20.0]
    assert compare(document)["location"] == document["location"]


def test_future_observations_do_not_become_long_lead_forecasts():
    from field_shift_models.global_water import compare

    document = json_document()
    scenario = document["scenarios"][0]
    scenario["kind"] = "historical_analogue"
    scenario["stationary_climate_assumption"] = True
    for row in scenario["weather"]:
        row["source_date"] = row["date"]
    with pytest.raises(ValueError, match="Future observations"):
        compare(document)


def test_cli_works_from_outside_checkout_and_protects_inputs(tmp_path):
    import json
    import os
    import subprocess
    import sys

    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(json_document()))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    command = [
        sys.executable,
        "-I",
        "-m",
        "field_shift_models.global_water",
        "--input",
        str(source),
        "--output",
    ]
    result = subprocess.run(
        command + [str(output)], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["promotion"] is False
    original = source.read_bytes()
    result = subprocess.run(
        command + [str(source)], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode != 0 and source.read_bytes() == original


def test_power_units_missing_days_and_total_precipitation_are_explicit():
    import copy

    from field_shift_science.layered_water.power import UNITS, parse_power

    document = {
        "header": {
            "time_standard": "UTC",
            "fill_value": -999.0,
            "start": "20240101",
            "end": "20240102",
        },
        "geometry": {"coordinates": [30.52, 50.45, 128.0]},
        "parameters": {k: {"units": v} for k, v in UNITS.items()},
        "properties": {
            "parameter": {
                k: {"20240101": v, "20240102": v}
                for k, v in {
                    "T2M_MAX": 10.0,
                    "T2M_MIN": 0.0,
                    "RH2M": 80.0,
                    "WS2M": 2.0,
                    "PRECTOTCORR": 5.0,
                    "ALLSKY_SFC_SW_DWN": 8.0,
                }.items()
            }
        },
    }
    rows = parse_power(document, source_checksum="a" * 64)
    assert len(rows) == 2 and all(r.et0_mm >= 0 and r.rain_mm == 5.0 for r in rows)
    bad = copy.deepcopy(document)
    bad["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "kW-hr/m^2/day"
    with pytest.raises(ValueError, match="units"):
        parse_power(bad, source_checksum="a" * 64)
    bad = copy.deepcopy(document)
    bad["properties"]["parameter"]["PRECTOTCORR"]["20240101"] = -999.0
    with pytest.raises(ValueError, match="Missing"):
        parse_power(bad, source_checksum="a" * 64)
    bad = copy.deepcopy(document)
    del bad["properties"]["parameter"]["WS2M"]["20240102"]
    with pytest.raises(ValueError, match="dates"):
        parse_power(bad, source_checksum="a" * 64)


def test_opening_snow_and_freeze_history_are_explicit_and_conserved():
    p = parameters(initial_snow_mm=20.0, prior_tmean_c=(-5.0,) * 7)
    wx = forcing(date(2027, 1, 1), date(2027, 1, 3), cold=True, rain=0.0)
    run = run_water_balance(p, wx, [], land_status="arable")
    assert run.days[0].frozen and run.closing_snow_mm == 20.0
    assert run.max_balance_error_mm < 1e-6
    with pytest.raises(ValueError, match="preceding"):
        replace(p, prior_tmean_c=())
