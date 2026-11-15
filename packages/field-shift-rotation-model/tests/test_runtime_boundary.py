import copy
import hashlib
import json
from datetime import UTC, datetime
from importlib.resources import files
from io import BytesIO
from unittest.mock import patch

import pytest

from field_shift_rotation_model import network, runtime
from field_shift_rotation_model.network import AcquisitionUnavailable, EvidenceSession


def test_network_denied_by_default_and_nonpublic_urls_rejected():
    with pytest.raises(AcquisitionUnavailable, match="explicit"):
        network.urlopen("https://api.open-meteo.com/v1/forecast")
    for url in (
        "http://api.open-meteo.com/",
        "https://localhost/",
        "https://user:pass@api.open-meteo.com/",
        "https://api.open-meteo.com:4433/",
    ):
        with pytest.raises(AcquisitionUnavailable):
            network.request_identity(url)


def test_exact_acquisition_bytes_replayed_without_network():
    raw = b'{"real_provider_fixture":1}'

    class FakeOpener:
        def open(self, *args, **kwargs):
            return BytesIO(raw)

    url = "https://api.open-meteo.com/v1/forecast?latitude=42&longitude=-93"
    with patch.object(network, "real_build_opener", return_value=FakeOpener()):
        with network.evidence_session(acquire=True) as session:
            assert network.urlopen(url).read() == raw
    row = next(iter(session.records.values()))
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    with patch.object(
        network, "real_build_opener", side_effect=AssertionError("no offline network")
    ):
        with network.evidence_session(records=session.records):
            assert network.urlopen(url).read() == raw
            with pytest.raises(AcquisitionUnavailable):
                network.urlopen(url + "&other=1")
    changed = copy.deepcopy(session.records)
    next(iter(changed.values()))["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="checksum"):
        EvidenceSession(records=changed)


def test_source_evidence_rejects_byte_overflow_before_recording():
    class Oversized:
        def open(self, *args, **kwargs):
            return BytesIO(b"x" * (network.MAX_RESPONSE + 1))

    with patch.object(network, "real_build_opener", return_value=Oversized()):
        with network.evidence_session(acquire=True) as session:
            with pytest.raises(AcquisitionUnavailable, match="byte"):
                network.urlopen("https://api.open-meteo.com/v1/forecast")
            assert session.records == {}


def test_catalog_and_reference_do_not_promote_or_claim_measured_profit():
    catalog = runtime.catalog()
    assert len(catalog["crops"]) == 13 and len(catalog["equipment"]) == 13
    assert sum(c["climate_supported"] for c in catalog["crops"]) == 7
    assert catalog["empirical_status"] == "not_validated"
    assert len(runtime.cultivar_references()) == 20
    assert runtime.ENGINE_VERSION.startswith("rotation-handoff-a68b67c-installed-v1:")


def test_actual_bundled_reference_acquires_and_replays_offline_exactly():
    today = datetime.now(UTC).date().isoformat()
    with patch.object(network, "real_build_opener", side_effect=AssertionError("no network")):
        snapshot = runtime.acquire(
            {
                "operation": "sutherland_2027",
                "parameters": {},
                "decision_date": today,
                "allow_public_data": False,
            }
        )
        result = runtime.evaluate(snapshot)
    expected = json.loads(
        files("field_shift_rotation_model")
        .joinpath("examples/sutherland-2026-28/result-nasa-2027.json")
        .read_text()
    )
    assert result["payload"] == expected
    assert result["data_kind"] == "conditional_climate"
    assert result["empirical_status"] == "not_validated"
    assert result["payload"]["ensemble"]["weight_kind"] == "planning_setting"
    assert result["source_receipts"] and result["evidence_sha256"] == snapshot["evidence_sha256"]
    modified = copy.deepcopy(snapshot)
    modified["parameters"] = {"tampered": True}
    with pytest.raises(ValueError, match="checksum"):
        runtime.evaluate(modified)


def test_new_acquisition_cannot_backdate_weather_vintage():
    with pytest.raises(ValueError, match="current UTC"):
        runtime.acquire(
            {
                "operation": "sutherland_2027",
                "parameters": {},
                "decision_date": "2020-01-01",
                "allow_public_data": True,
            }
        )


def test_missing_calendar_and_southern_climate_stay_unsupported():
    request = {"field": {"latitude": -22.91, "longitude": -43.17}, "planting_date": "2040-05-01"}
    with pytest.raises(ValueError):
        runtime.calculate("climate_step", request, datetime.now(UTC).date())
    request = {
        "field_id": "sutherland_nw",
        "seasons": [{"planting_date": "2027-05-01"}],
        "plans": [
            {"id": "a", "crops": [{"crop_id": "rapeseed_canola"}]},
            {"id": "b", "crops": [{"crop_id": "maize_grain"}]},
        ],
    }
    with pytest.raises(ValueError, match="calendar"):
        runtime.calculate("forecast", request, datetime(2026, 11, 15, tzinfo=UTC).date())


def test_temperature_gate_does_not_depend_on_latitude_or_treat_zero_deficit_as_good():
    from datetime import date
    from types import SimpleNamespace

    from field_shift_rotation_model.interpretation import interpretation, thermal_diagnostics

    crop = SimpleNamespace(
        crop_id="maize_grain",
        first_day=date(2027, 5, 1),
        last_day=date(2027, 9, 1),
        profile=SimpleNamespace(base_c=None, flowering_gdd=None),
    )
    cold = [SimpleNamespace(high_c=-5, low_c=-20)] * 120
    warm = [SimpleNamespace(high_c=25, low_c=15)] * 120
    blocked = thermal_diagnostics(crop, cold, 10)
    assert interpretation("arable", blocked)["ranking_usable"] is False
    assert interpretation("arable", blocked)["suitability_status"] == "no_thermal_growth"
    assert interpretation("arable", thermal_diagnostics(crop, warm, 0))["ranking_usable"] is False
    assert (
        interpretation("unknown", thermal_diagnostics(crop, warm, 100))["ranking_usable"] is False
    )
    assert (
        interpretation("arable", thermal_diagnostics(crop, warm, 100))["suitability_status"]
        == "arable_land_only"
    )


def test_climate_frost_or_incomplete_cycle_blocks_recommendation():
    from field_shift_rotation_model.interpretation import climate_diagnostics, interpretation

    value = {
        "crop_id": "maize_grain",
        "planting_date": "2040-05-01",
        "end_date": "2040-09-01",
        "end_reason": "killing frost",
        "metrics": {"demand_mm": 10},
    }
    assert not interpretation("arable", climate_diagnostics({"scenarios": [value]}))[
        "ranking_usable"
    ]
    value["end_reason"] = "maturity"
    assert climate_diagnostics(value) == []
    value["metrics"]["demand_mm"] = 0
    assert climate_diagnostics(value)
