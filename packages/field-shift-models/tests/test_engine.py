"""Controlled fixtures prove adapter invariants, never empirical model accuracy."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from field_shift_contracts.planning_v1.models import ModelCard, SequenceEvaluation
from pydantic import ValidationError

from field_shift_models.engine import Parameters, RejectedInput, forcing_frame, run_engine
from field_shift_models.fixtures import synthetic_inputs
from field_shift_models.provider import AquaCropProvider


def revise(model, change):
    data = model.model_dump(mode="json")
    change(data)
    return type(model).model_validate_json(json.dumps(data))


def test_ready_engine_contract_dry_basis_and_units():
    p, c, w, h = synthetic_inputs()
    provider = AquaCropProvider({p.snapshot_id: h})
    result = provider.evaluate(p, c, w)
    assert SequenceEvaluation.model_validate_json(result.model_dump_json()) == result
    card = provider.model_card()
    assert ModelCard.model_validate_json(card.model_dump_json()) == card
    assert card.promotion == "baseline_only" and card.validated_scope is None
    s = result.seasons[0]
    assert s.status == "available"
    assert s.harvest_basis.basis == "dry_matter" and s.harvest_basis.moisture_fraction == 0
    assert s.yield_t_ha.value > 0
    assert s.yield_t_ha.uncertainty.kind == "none" and s.yield_t_ha.lower is None
    assert 0 <= s.surface_moisture_m3_m3.value <= 1
    assert s.root_zone_reserve_mm.unit == "mm"
    assert s.root_zone_reserve_mm.value <= s.root_depth_cm * 10 * 0.15 + 1e-9
    assert s.surface_moisture_m3_m3.unit == "m3/m3"
    assert s.irrigation_mm.value == 0


def test_daily_conservation_and_state_continuity_without_january_reset():
    p, c, w, h = synthetic_inputs(years=3)
    result = run_engine(p, c.assignments, w.scenarios[0].traces[0].days, h)
    assert result.max_balance_error_mm < 1e-6
    assert len(result.harvests) == 3
    assert all((b - a).days == 1 for a, b in zip(result.dates, result.dates[1:]))
    assert date(2028, 1, 1) in result.dates
    january = result.dates.index(date(2028, 1, 1))
    # The carried January profile is the simulated profile, not the opening field capacity.
    assert not np.allclose(result.theta[january], 0.3)
    before = float((result.theta[january - 1] * result.dz_m * 1000).sum())
    after = float((result.theta[january] * result.dz_m * 1000).sum())
    day = result.flux.iloc[january]
    assert after == pytest.approx(
        before
        + 3
        + day.CR
        + day.GwIn
        - day.Runoff
        - day.DeepPerc
        - day.TileDrain
        - day.Es
        - day.Tr,
        abs=1e-6,
    )
    output = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w)
    assert output.seasons[0].closing_state_id == output.seasons[1].opening_state_id
    assert output.seasons[1].closing_state_id == output.seasons[2].opening_state_id


def test_multiseason_wet_germination_and_physical_dry_zero_are_not_fabricated():
    p, c, w, h = synthetic_inputs(years=3)
    wet = revise(
        w,
        lambda d: [
            day.update(liquid_rain_mm=6.0) for day in d["scenarios"][0]["traces"][0]["days"]
        ],
    )
    result = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, wet)
    assert all(s.yield_t_ha.value > 0 for s in result.seasons)
    dry = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w)
    # Zero is a real engine result under this dry seedbed, while status remains available.
    assert dry.seasons[1].status == "available" and dry.seasons[1].yield_t_ha.value == 0


@pytest.mark.parametrize(
    "crop,cultivar,profile_version",
    [
        ("Rice", "ospy-generic", "3.1.0"),
        ("Maize", "unseen-hybrid", "3.1.0"),
        ("Wheat", None, "3.1.0"),
        ("Maize", "ospy-generic", "unknown"),
    ],
)
def test_unsupported_cultivar_never_substitutes_crop(crop, cultivar, profile_version):
    p, c, w, h = synthetic_inputs()

    def change(data):
        ref = dict(crop_id=crop, cultivar_id=cultivar, profile_version=profile_version)
        data["assignments"][0]["crop"] = ref
        data["assessments"][0]["crop"] = ref

    c = revise(c, change)
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == "unsupported" and s.yield_t_ha.value is None
    assert s.reasons[0].code == "cultivar"


@pytest.mark.parametrize("capability", ["paddy", "fertility", "salinity", "inundation", "winter"])
def test_capabilities_are_explicit(capability):
    p, c, w, h = synthetic_inputs()
    c = revise(c, lambda d: d["assessments"][0].update(required_capabilities=[capability]))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == "unsupported" and s.reasons[0].code == "capability"


@pytest.mark.parametrize(
    "patch,status",
    [
        ({"liquid_rain_mm": None}, "insufficient_data"),
        ({"soil_frozen": None}, "insufficient_data"),
        ({"snow_water_equivalent_mm": None}, "insufficient_data"),
        ({"quality": "rejected"}, "insufficient_data"),
        ({"tmin_c": -1.0}, "unsupported"),
        ({"soil_frozen": True}, "unsupported"),
        ({"snow_water_equivalent_mm": 2.0}, "unsupported"),
    ],
)
def test_missing_and_winter_forcing_abstain(patch, status):
    p, c, w, h = synthetic_inputs()
    w = revise(w, lambda d: d["scenarios"][0]["traces"][0]["days"][0].update(patch))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == status and s.root_zone_reserve_mm.value is None


def test_zero_rain_is_preserved_and_missing_not_zero():
    _, _, w, _ = synthetic_inputs()
    day = w.scenarios[0].traces[0].days[0].model_copy(update={"liquid_rain_mm": 0.0})
    assert forcing_frame([day]).Precipitation.iloc[0] == 0
    with pytest.raises(RejectedInput, match="Complete daily forcing"):
        forcing_frame([day.model_copy(update={"liquid_rain_mm": None})])


@pytest.mark.parametrize("missing", ["soil", "water_state"])
def test_missing_soil_or_water_has_null_outputs(missing):
    p, c, w, h = synthetic_inputs()
    p = revise(p, lambda d: d.update({missing: [] if missing == "soil" else None}))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == "insufficient_data" and s.yield_t_ha.value is None


def test_hydraulic_parameters_never_default_and_no_soil_extension():
    p, c, w, h = synthetic_inputs()
    s = AquaCropProvider({}).evaluate(p, c, w).seasons[0]
    assert s.reasons[0].code == "hydraulics"
    p = revise(p, lambda d: d["soil"][0].update(bottom_cm=100.0))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.reasons[0].code == "soil_depth"


def test_real_field_never_gets_synthetic_hydraulics():
    p, c, w, h = synthetic_inputs()
    p = revise(p, lambda d: d["sources"][0].update(kind="measured"))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == "unsupported" and s.reasons[0].code == "synthetic_parameters"


def test_identity_and_distance_do_not_invent_subgrid_precision():
    p, c, w, h = synthetic_inputs()
    first = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    c = revise(c, lambda d: d.update(sequence_id="other-id"))
    w = revise(
        w,
        lambda d: d["scenarios"][0]["traces"][0].update(
            representative_latitude=-34.6, representative_longitude=-58.4
        ),
    )
    second = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    for key in ("yield_t_ha", "stress_days", "root_zone_reserve_mm", "surface_moisture_m3_m3"):
        assert getattr(first, key) == getattr(second, key)


def test_mismatched_scope_or_as_of_is_rejected():
    p, c, w, h = synthetic_inputs()
    provider = AquaCropProvider({p.snapshot_id: h})
    with pytest.raises(ValueError, match="scope"):
        provider.evaluate(p, c.model_copy(update={"field_id": "foreign-field"}), w)
    with pytest.raises(ValueError, match="as_of"):
        provider.evaluate(p.model_copy(update={"as_of": w.stale_after}), c, w)


def test_mixed_rotation_is_unsupported_not_repeated_maize():
    p, c, w, h = synthetic_inputs(years=3)

    def change(d):
        d["assignments"][1]["crop"]["crop_id"] = "Wheat"
        d["assessments"][1]["crop"]["crop_id"] = "Wheat"

    c = revise(c, change)
    result = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w)
    assert all(
        s.status == "unsupported" and s.reasons[0].code == "rotation" for s in result.seasons
    )


def test_explicit_irrigation_schedule_budget_and_conservation():
    p, c, w, h = synthetic_inputs(dry=True)
    p = revise(
        p, lambda d: d["management"]["irrigation"][0].update(system="sprinkler", max_mm=60.0)
    )
    no_schedule = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert no_schedule.status == "insufficient_data"
    h = replace(h, irrigation_events=((date(2027, 4, 15), 20.0), (date(2027, 5, 1), 20.0)))
    result = run_engine(p, c.assignments, w.scenarios[0].traces[0].days, h)
    assert result.max_balance_error_mm < 1e-6
    assert result.flux.IrrDay.sum() == pytest.approx(40.0)
    over_budget = replace(h, irrigation_events=((date(2027, 4, 15), 61.0),))
    s = AquaCropProvider({p.snapshot_id: over_budget}).evaluate(p, c, w).seasons[0]
    assert s.reasons[0].code == "irrigation_budget"


def test_future_source_is_rejected_by_canonical_contract():
    p, _, _, _ = synthetic_inputs()
    with pytest.raises(ValidationError, match="unavailable"):
        revise(
            p,
            lambda d: d["sources"][0].update(
                available_at="2027-04-02T00:00:00Z", retrieved_at="2027-04-02T00:00:00Z"
            ),
        )


def test_invalid_hydraulics():
    with pytest.raises(ValueError):
        Parameters((float("nan"),), (100.0,), "bad", True)


def test_opening_average_requires_explicit_assumption_and_coherent_units():
    p, c, w, h = synthetic_inputs()
    no_assumption = replace(h, uniform_initial_profile=False)
    s = AquaCropProvider({p.snapshot_id: no_assumption}).evaluate(p, c, w).seasons[0]
    assert s.status == "insufficient_data" and s.reasons[0].code == "opening_profile"
    p = revise(p, lambda d: d["water_state"].update(available_reserve_mm=36.0))
    s = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w).seasons[0]
    assert s.status == "unsupported" and s.reasons[0].code == "water_units"


def test_dry_opening_below_wilting_point_is_not_missing():
    p, c, w, h = synthetic_inputs(dry=True)
    p = revise(p, lambda d: d["water_state"].update(volumetric_m3_m3=0.1, available_reserve_mm=0.0))
    result = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w)
    assert result.seasons[0].status == "available"
    assert result.seasons[0].yield_t_ha.value == 0


def test_outside_configured_footprint_is_not_locally_validated():
    p, c, w, h = synthetic_inputs()
    p = revise(
        p,
        lambda d: d.update(
            terrain={
                "latitude_deg": -34.6,
                "longitude_deg": -58.4,
                "elevation_m": None,
                "slope_deg": None,
                "aspect_deg": None,
                "aspect_convention": "clockwise_from_true_north",
                "solar_time": None,
                "solar_zenith_deg": None,
                "surface_shortwave_w_m2": None,
                "source_ids": [],
            }
        ),
    )
    result = AquaCropProvider({p.snapshot_id: h}, footprint=(20.0, 40.0, 40.0, 60.0)).evaluate(
        p, c, w
    )
    assert result.applicability == "out_of_domain"
    assert (
        result.seasons[0].status == "out_of_domain" and result.seasons[0].yield_t_ha.value is None
    )


def test_offline_parameter_trial_uses_ready_engine_without_changing_generic():
    p, c, w, h = synthetic_inputs()
    generic = run_engine(p, c.assignments, w.scenarios[0].traces[0].days, h)
    trial = run_engine(p, c.assignments, w.scenarios[0].traces[0].days, h, crop_wp=30.0)
    again = run_engine(p, c.assignments, w.scenarios[0].traces[0].days, h)
    key = "Dry yield (tonne/ha)"
    assert trial.harvests.iloc[0][key] != generic.harvests.iloc[0][key]
    assert again.harvests.iloc[0][key] == generic.harvests.iloc[0][key]


def test_preexisting_profile_accepts_later_weather_without_rewriting_time():
    from datetime import timedelta

    p, c, w, h = synthetic_inputs()
    later = revise(w, lambda d: d.update(as_of=(w.as_of + timedelta(hours=1)).isoformat()))
    provider = AquaCropProvider({p.snapshot_id: h})
    before = p.model_dump_json()
    result = provider.evaluate(p, c, later)
    assert result.as_of == later.as_of
    assert result.seasons[0].status == "available"
    assert p.model_dump_json() == before
    future = p.model_copy(update={"as_of": later.as_of + timedelta(hours=1)})
    with pytest.raises(ValueError, match="unavailable at the weather issue"):
        provider.evaluate(future, c, later)


@pytest.mark.parametrize("crop", ["Soy", "Sunflower"])
def test_pinned_native_crop_profiles_are_calculated_without_species_substitution(crop):
    from aquacrop import Crop

    from field_shift_models.engine import NATIVE_CROP_NAMES

    p, candidate, weather, hydraulics = synthetic_inputs(crop=crop)
    actual = AquaCropProvider({p.snapshot_id: hydraulics})
    card = actual.model_card()
    assert crop in {c.crop_id for c in card.supported_crops}
    upstream = Crop(NATIVE_CROP_NAMES[crop], planting_date="04/01")
    assert upstream.Name == ("Soybean" if crop == "Soy" else "Sunflower")
    result = actual.evaluate(p, candidate, weather)
    assert result.applicability == "baseline"
    season = result.seasons[0]
    assert season.crop.crop_id == crop and season.status == "available"
    assert np.isfinite(season.yield_t_ha.value) and season.yield_t_ha.value >= 0
    assert season.root_zone_reserve_mm.value >= 0
    assert card.validated_scope is None and card.promotion == "baseline_only"


@pytest.mark.parametrize("crop", ["Rapeseed", "Peas", "Rice"])
def test_missing_native_profiles_stay_unsupported(crop):
    p, candidate, weather, hydraulics = synthetic_inputs(crop=crop)
    result = AquaCropProvider({p.snapshot_id: hydraulics}).evaluate(p, candidate, weather)
    assert result.seasons[0].status == "unsupported"
    assert result.seasons[0].yield_t_ha.value is None


@pytest.mark.parametrize("defect", ["unlinked", "partial", "zero", "point", "future_retrieval"])
def test_direct_provider_rejects_unproven_land_before_numeric_execution(monkeypatch, defect):
    from datetime import timedelta

    from field_shift_models import provider as module

    p, c, w, h = synthetic_inputs()
    if defect == "unlinked":
        p = p.model_copy(update={"land_source_ids": []})
    else:
        source = next(s for s in p.sources if s.id in p.land_source_ids)
        if defect == "partial":
            source.quality = "partial"
        elif defect == "zero":
            source.coverage_fraction = 0
        elif defect == "point":
            source.spatial_method = "representative_point"
        else:
            source.retrieved_at = w.as_of + timedelta(days=1)

    def forbidden(*args, **kwargs):
        raise AssertionError("unqualified land must not execute the numerical model")

    monkeypatch.setattr(module, "run_engine", forbidden)
    result = AquaCropProvider({p.snapshot_id: h}).evaluate(p, c, w)
    assert all(
        s.status == "insufficient_data" and s.yield_t_ha.value is None for s in result.seasons
    )
    assert any(r.code in {"land_evidence", "source_quality"} for r in result.reasons)
