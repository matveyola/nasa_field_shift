"""Controlled A predicates. These assertions do not establish empirical accuracy."""

import json
from datetime import date

from field_shift_contracts.planning_v1 import FarmPlanningRequest, WeatherScenarioSet
from field_shift_contracts.planning_v1.common import CropRef
from field_shift_contracts.planning_v1.field import CropAssignment

from field_shift.modules.fields.planning_profile.providers import inundation_impact
from field_shift.modules.rotations.suitability.engine import SuitabilityEngine
from field_shift.modules.rotations.suitability.rules import CropRule, Rulebook


def prepared(profile):
    return profile.model_copy(
        update={
            "land_status": "arable",
            "soil": [profile.soil[0].model_copy(update={"ph": 6.5, "quality": "valid"})],
            "management": profile.management.model_copy(
                update={
                    "irrigation": [
                        profile.management.irrigation[0].model_copy(
                            update={
                                "season": 2027,
                                "system": "none",
                                "max_mm": 0.0,
                            }
                        )
                    ],
                }
            ),
        }
    )


def crop(cultivar="early"):
    return CropRef(crop_id="Wheat", cultivar_id=cultivar, profile_version="synthetic-v1")


def rule(*, cultivar="early", dd=600, start="2027-05-15", end="2027-09-01", **overrides):
    doc = dict(
        id=f"synthetic-{cultivar}",
        crop=crop(cultivar).model_dump(mode="json"),
        family="poaceae",
        region="controlled",
        source="case-bindings.v1.json:thermal-time-v1",
        review_status="synthetic_test",
        valid_window={"start": start, "end": end},
        purpose="new_sowing",
        sowing_window={"start": start, "end": start},
        growth_window={"start": start, "end": end},
        base_temperature_c=5.0,
        required_degree_days=dd,
        require_unfrozen_sowing=True,
        min_seasonal_supply_mm=0.0,
        min_return_years=1,
        family_return_years=1,
        min_ph=5.0,
        max_ph=8.0,
        min_soil_depth_cm=30.0,
        irrigation_systems=["none", "sprinkler"],
        required_capabilities=["phenology"],
    )
    doc.update(overrides)
    return CropRule.model_validate_json(json.dumps(doc))


def engine(*rules, allow_synthetic=True):
    return SuitabilityEngine(
        Rulebook(version="controlled-v1", rules=list(rules)),
        region_for=lambda p: "controlled",
        allow_synthetic=allow_synthetic,
    )


def weather_for(examples, profile, records):
    doc = examples["weather"]
    doc["scenario_type"] = "synthetic"
    doc["valid_window"] = {"start": records[0]["date"], "end": records[-1]["date"]}
    trace = doc["scenarios"][0]["traces"][0]
    trace.update(field_snapshot_id=profile.snapshot_id, days=records)
    return WeatherScenarioSet.model_validate_json(json.dumps(doc))


def assess(profile, examples, records, selected_rule=None):
    r = selected_rule or rule()
    return engine(r).evaluate(profile, r.crop, 2027, weather_for(examples, profile, records))


def test_G01_G02_G16_land_and_absence(profile):
    e = engine(rule())
    for land in ["ice", "ocean", "non_arable"]:
        assessment = e.evaluate(
            profile.model_copy(update={"land_status": land}), crop(), 2027, None
        )
        assert assessment.status == "ineligible"
        assert "non_arable" in [r.code for r in assessment.reasons]
    missing = e.evaluate(
        profile.model_copy(update={"land_status": "unknown", "sources": []}), crop(), 2027, None
    )
    assert missing.status == "insufficient_data"
    assert {"land_unknown", "weather_missing"} <= {r.code for r in missing.reasons}


def test_G03_early_late_thermal_time(profile, examples, fixtures):
    print_keys = fixtures["records"]
    records = print_keys[next(k for k in print_keys if "G03" in k)]
    # Frozen recipes omit optional soil state. This explicit controlled addition
    # is a test assumption, never an observation or a production imputation.
    records = [{**d, "soil_frozen": False} for d in records]
    p = prepared(profile)
    assert assess(p, examples, records).status == "eligible"
    late = rule(cultivar="late", dd=2000)
    assert assess(p, examples, records, late).status == "ineligible"


def test_G04_new_sowing_and_overwintering_differ(profile, examples, fixtures):
    records = fixtures["records"][next(k for k in fixtures["records"] if "G04" in k)]
    p = prepared(profile)
    start, end = records[0]["date"], records[-1]["date"]
    r = rule(start=start, end=end, dd=0)
    new = assess(p, examples, records, r)
    assert new.status == "ineligible"
    assert "frozen_sowing" in [x.code for x in new.reasons]
    winter = assess(
        p,
        examples,
        records,
        r.model_copy(
            update={
                "purpose": "overwintering",
                "required_capabilities": ["winter", "snow", "frozen_soil"],
            }
        ),
    )
    assert "frozen_sowing" not in [x.code for x in winter.reasons]
    assert "winter" in winter.required_capabilities


def test_G07_G08_soil_cannot_override_water_or_cultivar(profile, examples, fixtures):
    p = prepared(profile)
    records = [{**d, "soil_frozen": False} for d in fixtures["records"]["hot_dry"]]
    limited = rule(min_seasonal_supply_mm=300.0)
    a = assess(p, examples, records, limited)
    assert a.status == "ineligible"
    assert "water_supply" in [r.code for r in a.reasons]
    assert not hasattr(a, "yield_t_ha")
    assert "fertility" not in a.required_capabilities


def test_G12_G14_matched_inputs_ignore_ids_and_distance(profile, examples, fixtures):
    p = prepared(profile)
    records = [{**d, "soil_frozen": False} for d in fixtures["records"]["temperate_growth"]]
    first = assess(p, examples, records)
    changed = p.model_copy(update={"field_id": "far-away", "snapshot_id": "other"})
    second = assess(changed, examples, records)
    assert first.status == second.status
    assert first.reasons == second.reasons


def test_G17_zero_rain_remains_zero_missing_stays_unknown(profile, examples, fixtures):
    p = prepared(profile)
    records = [
        {**d, "soil_frozen": False, "liquid_rain_mm": 0.0}
        for d in fixtures["records"]["temperate_growth"]
    ]
    r = rule(min_seasonal_supply_mm=1.0)
    assert assess(p, examples, records, r).status == "ineligible"
    missing = [{**d, "liquid_rain_mm": None} for d in records]
    assert assess(p, examples, missing, r).status == "insufficient_data"


def test_G20_unknown_cultivar_and_unreviewed_rules(profile):
    p = prepared(profile)
    assert engine(rule()).evaluate(p, crop("unknown-hybrid"), 2027, None).status == "unsupported"
    assert engine(rule(), allow_synthetic=False).evaluate(p, crop(), 2027, None).status == (
        "insufficient_data"
    )
    assert engine(rule(review_status="unreviewed")).evaluate(p, crop(), 2027, None).status == (
        "insufficient_data"
    )


def test_G22_no_hydraulic_model_or_probability_from_river_index(profile):
    assert inundation_impact([]) == "unsupported"
    assert inundation_impact(profile.hazards) == "unsupported"


def test_candidates_respect_locks_and_bound(profile, examples):
    p = prepared(profile)
    r = rule(start="2027-04-01", end="2027-04-01", dd=0, require_unfrozen_sowing=False)
    doc = examples["request"]
    doc.update(
        fields=[p.model_dump(mode="json")],
        enabled_crops=[crop().model_dump(mode="json")],
        rulebook_version="controlled-v1",
    )
    doc["weather"]["scenarios"][0]["traces"][0]["field_snapshot_id"] = p.snapshot_id
    lock = CropAssignment(
        field_id=p.field_id,
        season=2027,
        crop=crop(),
        sowing_window=None,
        harvest_window=None,
        locked=True,
        commitment_id="committed",
    )
    doc["locks"] = [lock.model_dump(mode="json")]
    doc["economics"] = []
    req = FarmPlanningRequest.model_validate_json(json.dumps(doc))
    sequences = engine(r).candidates(req)
    assert len(sequences) == 1
    assert sequences[0].assignments[0] == lock
    assert sequences[0].assessments[0].status == "eligible"


def test_return_history_unknown_and_confirmed(profile, examples, fixtures):
    from field_shift_contracts.planning_v1.field import CropHistoryEntry

    p = prepared(profile)
    records = [{**d, "soil_frozen": False} for d in fixtures["records"]["temperate_growth"]]
    r = rule(min_return_years=2, family_return_years=2)
    assert assess(p, examples, records, r).status == "insufficient_data"
    entry = CropHistoryEntry(
        season=2026,
        crop=crop(),
        status="confirmed",
        sowing_date=None,
        harvest_date=None,
        source_ids=[p.sources[0].id],
    )
    prior = p.model_copy(update={"history": [entry]})
    assert assess(prior, examples, records, r).status == "ineligible"


def test_partial_weather_and_missing_window_abstain(profile, examples):
    p = prepared(profile)
    weather = WeatherScenarioSet.model_validate_json(json.dumps(examples["weather"]))
    assert engine(rule()).evaluate(p, crop(), 2027, weather).status == "insufficient_data"


def test_G01_cold_evidence_not_continent_rule(profile, examples):
    from datetime import timedelta

    p = prepared(profile)
    # G01 pack intentionally has no implicit year: explicitly declare this window.
    records = [
        {
            "date": (date(2027, 5, 15) + timedelta(days=i)).isoformat(),
            "source_date": None,
            "lead_days": None,
            "tmin_c": -30.0,
            "tmax_c": -15.0,
            "liquid_rain_mm": 0.0,
            "et0_mm": 0.0,
            "shortwave_mj_m2": 0.0,
            "wind_m_s": None,
            "relative_humidity_fraction": None,
            "snow_water_equivalent_mm": None,
            "soil_frozen": True,
            "quality": "partial",
        }
        for i in range(3)
    ]
    result = assess(p, examples, records, rule(end="2027-05-17"))
    assert result.status == "ineligible"
    assert {"thermal_time", "frozen_sowing"} <= {r.code for r in result.reasons}


def test_G13_inputs_preserve_storage_and_irrigation_without_yield_claim(profile):
    from field_shift_contracts.planning_v1 import FieldProfileSnapshot

    # Domain stress ordering is owned by B. A preserves explicit differing inputs.
    doc = profile.model_dump(mode="json")
    original = doc["water_state"]["available_reserve_mm"]
    doc["water_state"]["available_reserve_mm"] = 0.0
    doc["management"]["irrigation"][0]["max_mm"] = 0.0
    changed = FieldProfileSnapshot.model_validate_json(json.dumps(doc))
    assert changed.water_state.available_reserve_mm == 0
    assert profile.water_state.available_reserve_mm == original
    assert changed.soil == profile.soil
    assert changed.water_state.volumetric_m3_m3 == profile.water_state.volumetric_m3_m3


def test_three_season_candidates_carry_choices_into_return_rules(profile, examples):
    from datetime import timedelta

    p = prepared(profile)
    p = p.model_copy(
        update={
            "management": p.management.model_copy(
                update={
                    "irrigation": [
                        p.management.irrigation[0].model_copy(update={"season": year})
                        for year in [2027, 2028, 2029]
                    ],
                }
            )
        }
    )
    wheat = crop()
    soy = wheat.model_copy(update={"crop_id": "Soy"})
    rules = []
    for year in [2027, 2028, 2029]:
        for selected, family in [(wheat, "poaceae"), (soy, "fabaceae")]:
            r = rule(
                start=f"{year}-04-01",
                end=f"{year}-04-01",
                dd=0,
                require_unfrozen_sowing=False,
                min_return_years=1 if year == 2027 else 2,
                family_return_years=1 if year == 2027 else 2,
            )
            rules.append(
                r.model_copy(
                    update={
                        "id": f"test-{selected.crop_id}-{year}",
                        "crop": selected,
                        "family": family,
                    }
                )
            )
    doc = examples["request"]
    doc.update(
        season_count=3,
        fields=[p.model_dump(mode="json")],
        enabled_crops=[wheat.model_dump(mode="json"), soy.model_dump(mode="json")],
        rulebook_version="controlled-v1",
        economics=[],
    )
    template = doc["weather"]["scenarios"][0]["traces"][0]["days"][0]
    first, last = date(2027, 4, 1), date(2029, 4, 1)
    # Explicit synthetic complete window, never production weather expansion.
    records = [
        {**template, "date": (first + timedelta(days=i)).isoformat(), "lead_days": None}
        for i in range((last - first).days + 1)
    ]
    weather = weather_for(examples, p, records)
    doc["weather"] = weather.model_dump(mode="json")
    doc["locks"] = [
        CropAssignment(
            field_id=p.field_id,
            season=2027,
            crop=wheat,
            sowing_window=None,
            harvest_window=None,
            locked=True,
            commitment_id="wheat-committed",
        ).model_dump(mode="json")
    ]
    req = FarmPlanningRequest.model_validate_json(json.dumps(doc))
    sequences = engine(*rules).candidates(req)
    assert len(sequences) == 4
    alternating = next(
        s for s in sequences if [a.crop.crop_id for a in s.assignments] == ["Wheat", "Soy", "Wheat"]
    )
    assert [a.status for a in alternating.assessments] == ["eligible"] * 3
    repeat = next(s for s in sequences if [a.crop.crop_id for a in s.assignments] == ["Wheat"] * 3)
    assert repeat.assessments[1].status == "ineligible"
    assert repeat.assessments[2].status == "ineligible"
    assert all(s.assignments[0].commitment_id == "wheat-committed" for s in sequences)
