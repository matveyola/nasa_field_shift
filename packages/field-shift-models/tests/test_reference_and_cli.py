"""Named reference, controlled deficit counterfactuals and private process boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from field_shift_science.crops.catalog import demo_crop_profile
from field_shift_science.simulation.water_balance import fao56_days

from field_shift_models.cli import bounded_worker, stable_report
from field_shift_models.comparison import wofost_synthetic
from field_shift_models.fixtures import synthetic_inputs


def reference(capacity, irrigation=0):
    _, _, weather, _ = synthetic_inputs(dry=True)
    forcing = [
        SimpleNamespace(date=d.date, rain_mm=d.liquid_rain_mm, reference_et0_mm=d.et0_mm)
        for d in weather.scenarios[0].traces[0].days
    ]
    return fao56_days(
        forcing,
        initial_mm=capacity,
        capacity_mm=capacity,
        threshold_mm=capacity * 0.5,
        coefficient=demo_crop_profile("Maize").water_use_factor,
        irrigation_mm=irrigation,
    )


def test_G05_G06_controlled_deficit_irrigation_and_mass_balance():
    dry, irrigated = reference(180), reference(180, 20)
    assert sum(d.reserve_mm < 90 for d in irrigated) <= sum(d.reserve_mm < 90 for d in dry)
    assert sum(d.irrigation_mm for d in dry) == 0
    assert sum(d.irrigation_mm for d in irrigated) == 20
    for day in dry + irrigated:
        assert day.opening_mm + day.rain_mm + day.irrigation_mm == pytest.approx(
            day.reserve_mm + day.water_use_mm + day.drainage_mm, abs=1e-4
        )


def test_G13_storage_does_not_create_earlier_stress_in_controlled_deficit():
    low, high = reference(60), reference(180)
    assert next(d.date for d in high if d.reserve_mm < 90) >= next(
        d.date for d in low if d.reserve_mm < 30
    )


def test_real_pcse_engine_offline_benchmark_not_a_commercial_default():
    result = wofost_synthetic("Maize")
    assert result["yield_dry_t_ha"] >= 0
    assert result["parameter_permission"].startswith("unverified")
    assert len(result["input_sha256"]) == 64


def test_bounded_private_contract_inference(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.chdir(tmp_path)
    p, c, w, h = synthetic_inputs()
    payload = {
        "profile": p.model_dump(mode="json"),
        "candidate": c.model_dump(mode="json"),
        "weather": w.model_dump(mode="json"),
        "hydraulics": vars(h),
    }
    output = bounded_worker("evaluate", payload)
    assert output["result"]["evaluation"]["seasons"][0]["status"] == "available"
    assert output["resources"]["peak_rss_kib"] > 0
    assert output["resources"]["cpu_seconds"] < 30
    again = bounded_worker("evaluate", payload)
    assert stable_report(output) == stable_report(again)


def test_private_payload_bound_precedes_execution():
    with pytest.raises(ValueError, match="16 MiB"):
        bounded_worker("evaluate", {"oversize": "x" * (16 * 1024 * 1024)})


@pytest.mark.parametrize(
    "previous,current", [("cpu-v1.json", "cpu-v2.json"), ("cpu-v2.json", "cpu-v3.json")]
)
def test_packaging_and_chronology_preserve_frozen_baseline_numerics(previous, current):
    import json

    reports = Path(__file__).resolve().parents[1] / "reports"
    old = json.loads((reports / previous).read_text())
    new = json.loads((reports / current).read_text())
    assert old["accuracy_metrics"] is new["accuracy_metrics"] is None
    assert old["promotion"] == new["promotion"] == "baseline_only"
    for key, previous in old["runs"].items():
        current = new["runs"][key]
        assert current["input_sha256"] == previous["input_sha256"]
        for a, b in zip(
            previous["ospy"]["result"]["seasons"], current["ospy"]["result"]["seasons"], strict=True
        ):
            # These IDs deliberately incorporate the changed adapter source hash.
            identity = {"opening_state_id", "closing_state_id"}
            assert {k: v for k, v in a.items() if k not in identity} == {
                k: v for k, v in b.items() if k not in identity
            }
