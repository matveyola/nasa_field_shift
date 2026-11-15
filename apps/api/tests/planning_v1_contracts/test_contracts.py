import copy
import json
from datetime import date
from pathlib import Path

import pytest
from field_shift_contracts.planning_v1 import (
    CandidateSequence,
    FarmPlanningRequest,
    FieldProfileSnapshot,
    ModelCard,
    PlanningContracts,
    PlanningRun,
    SequenceEvaluation,
    TrainingObservation,
    WeatherScenarioSet,
)
from field_shift_contracts.planning_v1.acceptance import (
    AcceptancePack,
    EmpiricalManifest,
    WeatherRecipe,
    expand_recipe,
)
from field_shift_contracts.planning_v1.common import DateWindow, Metric
from field_shift_contracts.planning_v1.export import schema
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[4]
PACK = ROOT / "contracts/planning-v1"


def read(name):
    return json.loads((PACK / name).read_text())


def validate(model, data):
    return TypeAdapter(model).validate_json(json.dumps(data))


@pytest.fixture
def example():
    return read("examples/planning-contracts.json")


@pytest.mark.parametrize(
    "name", [k for k in PlanningContracts.model_fields if k != "schema_version"]
)
def test_public_dto_roundtrip(example, name):
    model = PlanningContracts.model_fields[name].annotation
    parsed = validate(model, example[name])
    assert validate(model, json.loads(parsed.model_dump_json())) == parsed


def test_aggregate_roundtrip(example):
    assert validate(PlanningContracts, example).model_dump(mode="json") == example


@pytest.mark.parametrize(
    "model,path",
    [
        (PlanningContracts, "planning-v1.schema.json"),
        (AcceptancePack, "cases.schema.json"),
        (EmpiricalManifest, "empirical-sites.schema.json"),
    ],
)
def test_generated_schema_is_current(model, path):
    assert (PACK / path).read_text() == schema(model)


def set_path(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.mark.parametrize(
    "name,path,replacement",
    [
        ("field_profile", ["area_ha"], "1"),
        ("field_profile", ["area_ha"], 0),
        ("field_profile", ["as_of"], "2026-11-14T01:00:00+01:00"),
        ("field_profile", ["geometry", "coordinates", 0, 1, 1], 91),
        ("field_profile", ["geometry", "coordinates", 0, 4, 0], 31),
        ("field_profile", ["soil", 0, "bottom_cm"], 0),
        ("field_profile", ["soil", 0, "wilting_point_m3_m3"], 0.9),
        ("field_profile", ["soil", 0, "sand_fraction"], 1),
        ("field_profile", ["soil", 0, "source_ids"], ["missing-source"]),
        ("field_profile", ["sources", 0, "available_at"], "2026-11-15T00:00:00Z"),
        ("field_profile", ["water_state", "available_reserve_mm"], 151),
        ("field_profile", ["management", "irrigation", 0, "max_mm"], 1),
        ("weather", ["issued_at"], "2026-11-15T00:00:00Z"),
        ("weather", ["scenarios", 0, "weight"], 1),
        ("weather", ["scenarios", 0, "traces", 0, "days", 0, "tmin_c"], 30),
        ("weather", ["scenarios", 0, "traces", 0, "days", 0, "liquid_rain_mm"], -1),
        ("weather", ["valid_window", "end"], "2027-04-02"),
        ("weather", ["scenarios", 0, "traces", 0, "source_id"], "missing"),
        ("candidate", ["assignments", 0, "field_id"], "other-field"),
        ("candidate", ["assessments", 0, "rulebook_version"], "other-rules"),
        ("candidate", ["assignments", 0, "locked"], True),
        ("request", ["fields", 0, "farm_id"], "other-farm"),
        ("request", ["as_of"], "2026-11-16T00:00:00Z"),
        ("request", ["goal"], "reference_margin"),
        ("request", ["resources", 0, "season"], 2028),
        ("request", ["resources", 0, "budget"], 100),
        ("request", ["solver_limits", "max_seconds"], 301),
        ("request", ["season_count"], 4),
        ("evaluation", ["seasons", 0, "yield_t_ha", "value"], 0),
        ("evaluation", ["seasons", 0, "root_zone_reserve_mm", "unit"], "m3/m3"),
        ("evaluation", ["seasons", 0, "surface_moisture_m3_m3", "unit"], "mm"),
        ("model_card", ["promotion"], "passed"),
        ("model_card", ["holdout_opened_at"], "2026-11-13T00:00:00Z"),
        ("observation", ["depth_bottom_cm"], 0),
        ("observation", ["unit"], "mm"),
        ("observation", ["value"], 1.1),
        ("observation", ["available_at"], "2026-11-13T00:00:00Z"),
        ("comparison", ["solver_status"], "optimal"),
        ("run", ["status"], "succeeded"),
        ("run", ["status"], "failed"),
        ("apply_request", ["confirmed_by_human"], False),
        ("apply_result", ["status"], "applied"),
    ],
)
def test_invalid_wire_payloads_are_rejected(example, name, path, replacement):
    data = example[name]
    set_path(data, path, replacement)
    with pytest.raises(ValidationError):
        validate(PlanningContracts.model_fields[name].annotation, data)


def test_unknown_cultivar_remains_representable_for_explicit_unsupported(example):
    suitability = example["suitability"]
    suitability["crop"] = {
        "crop_id": "unseen-species",
        "cultivar_id": "unseen-hybrid",
        "profile_version": None,
    }
    suitability["status"] = "unsupported"
    parsed = validate(PlanningContracts.model_fields["suitability"].annotation, suitability)
    assert parsed.crop.cultivar_id == "unseen-hybrid"


def test_zero_and_missing_are_distinct(example):
    day = validate(WeatherScenarioSet, example["weather"]).scenarios[0].traces[0].days[0]
    assert day.liquid_rain_mm == 0
    assert day.wind_m_s is None
    assert validate(SequenceEvaluation, example["evaluation"]).seasons[0].yield_t_ha.value is None


def test_extra_keys_and_nonfinite_numbers_are_rejected(example):
    data = example["field_profile"]
    data["secret_unreviewed_field"] = True
    with pytest.raises(ValidationError):
        validate(FieldProfileSnapshot, data)
    del data["secret_unreviewed_field"]
    data["area_ha"] = float("nan")
    with pytest.raises(ValidationError):
        validate(FieldProfileSnapshot, data)


def test_future_data_rejected_even_if_retrieval_is_valid(example):
    data = example["field_profile"]
    data["sources"][0].update(
        available_at="2026-11-15T00:00:00Z", retrieved_at="2026-11-15T00:00:00Z"
    )
    with pytest.raises(ValidationError, match="unavailable at as_of"):
        validate(FieldProfileSnapshot, data)


def test_forecast_lead_uses_cycle_not_label(example):
    weather = example["weather"]
    weather["scenario_type"] = "forecast"
    day = weather["scenarios"][0]["traces"][0]["days"][0]
    with pytest.raises(ValidationError, match="lead"):
        validate(WeatherScenarioSet, weather)
    day["lead_days"] = (date(2027, 4, 1) - date(2026, 11, 14)).days
    validate(WeatherScenarioSet, weather)


def test_scenarios_share_field_scope_and_weights_are_normalized(example):
    weather = example["weather"]
    other = copy.deepcopy(weather["scenarios"][0])
    other["trajectory_id"] = "second"
    weather["scenarios"].append(other)
    weather["weight_calibration"] = "synthetic validator exercise, not a probability product"
    for scenario in weather["scenarios"]:
        scenario["weight"] = 0.5
    validate(WeatherScenarioSet, weather)
    other["weight"] = 0.6
    with pytest.raises(ValidationError, match="sum to 1"):
        validate(WeatherScenarioSet, weather)
    other["weight"] = 0.5
    other["traces"][0]["field_snapshot_id"] = "different-field"
    with pytest.raises(ValidationError, match="same fields"):
        validate(WeatherScenarioSet, weather)


def test_soil_horizons_do_not_overlap(example):
    profile = example["field_profile"]
    profile["soil"].append(copy.deepcopy(profile["soil"][0]))
    with pytest.raises(ValidationError, match="overlap"):
        validate(FieldProfileSnapshot, profile)


def test_candidate_seasons_are_consecutive(example):
    candidate = example["candidate"]
    candidate["assignments"].append({**candidate["assignments"][0], "season": 2029})
    candidate["assessments"].append({**candidate["assessments"][0], "season": 2029})
    with pytest.raises(ValidationError, match="consecutive"):
        validate(CandidateSequence, candidate)
    candidate["assignments"][1]["season"] = candidate["assessments"][1]["season"] = 2028
    validate(CandidateSequence, candidate)


def test_model_card_keeps_farms_and_grids_out_of_other_partitions(example):
    card = example["model_card"]
    cohort = dict(
        partition="fit",
        region_ids=["region"],
        farm_group_ids=["farm-a"],
        grid_group_ids=["grid-a"],
        crops=["Wheat"],
        years=[2024],
        observation_count=10,
        metrics={},
    )
    card["cohorts"] = [cohort, {**cohort, "partition": "test", "years": [2025]}]
    with pytest.raises(ValidationError, match="overlap"):
        validate(ModelCard, card)
    card["cohorts"][1].update(farm_group_ids=["farm-b"], grid_group_ids=["grid-b"])
    validate(ModelCard, card)


def test_available_yield_requires_basis_and_water_requires_depth(example):
    evaluation = example["evaluation"]
    season = evaluation["seasons"][0]
    season["yield_t_ha"].update(status="available", value=2, reasons=[])
    with pytest.raises(ValidationError, match="harvest moisture basis"):
        validate(SequenceEvaluation, evaluation)
    season["harvest_basis"] = {"basis": "standard_moisture", "moisture_fraction": 0.14}
    season["root_zone_reserve_mm"].update(status="available", value=100, reasons=[])
    with pytest.raises(ValidationError, match="root depth"):
        validate(SequenceEvaluation, evaluation)
    season["root_depth_cm"] = 100
    validate(SequenceEvaluation, evaluation)


def test_evaluation_carries_state_between_years(example):
    evaluation = example["evaluation"]
    first = evaluation["seasons"][0]
    first.update(status="available", closing_state_id="state-after-2027")
    second = {**copy.deepcopy(first), "season": 2028, "opening_state_id": "reset-state"}
    evaluation["seasons"].append(second)
    with pytest.raises(ValidationError, match="carried state"):
        validate(SequenceEvaluation, evaluation)
    second["opening_state_id"] = "state-after-2027"
    validate(SequenceEvaluation, evaluation)


def test_predictive_interval_and_scenario_spread_are_different(example):
    metric = example["evaluation"]["seasons"][0]["yield_t_ha"]
    metric.update(status="available", value=3, lower=2, upper=5, reasons=[])
    metric["uncertainty"].update(kind="scenario_range", coverage=0.9)
    with pytest.raises(ValidationError, match="probability"):
        validate(Metric, metric)
    metric["uncertainty"].update(kind="predictive_interval")
    with pytest.raises(ValidationError, match="calibration scope"):
        validate(Metric, metric)
    metric["uncertainty"]["calibration_scope"] = "Synthetic schema check only"
    validate(Metric, metric)


def test_successful_run_cannot_attach_another_request(example):
    run = example["run"]
    run.update(status="succeeded", comparison=example["comparison"])
    validate(PlanningRun, run)
    run["comparison"]["request_id"] = "other-request"
    with pytest.raises(ValidationError, match="immutable run"):
        validate(PlanningRun, run)


def test_budget_conflict_is_domain_infeasibility_not_schema_rejection(example):
    request = example["request"]
    request["resources"][0].update(budget=0, currency="UAH")
    request["locks"] = [
        {
            **example["candidate"]["assignments"][0],
            "locked": True,
            "commitment_id": "seed-purchased",
        }
    ]
    parsed = validate(FarmPlanningRequest, request)
    assert parsed.locks[0].locked
    assert parsed.resources[0].budget == 0


def test_observation_null_is_not_a_valid_zero_label(example):
    observation = example["observation"]
    observation["value"] = None
    with pytest.raises(ValidationError, match="needs a value"):
        validate(TrainingObservation, observation)
    observation["quality"] = "missing"
    assert validate(TrainingObservation, observation).value is None


def test_all_24_cases_are_specifications_not_accuracy_results():
    pack = validate(AcceptancePack, read("cases.v1.json"))
    assert len(pack.cases) == 24
    assert all(not c.agronomist_reviewed for c in pack.cases)
    assert pack.status == "test_specification_not_executed"


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unknown_pair", "unknown_weather", "reversed_window"]
)
def test_acceptance_reference_failures(mutation):
    pack = read("cases.v1.json")
    if mutation == "duplicate":
        pack["cases"][1]["id"] = "G01"
    elif mutation == "unknown_pair":
        pack["cases"][1]["paired_with"] = "G99"
    elif mutation == "unknown_weather":
        pack["cases"][0]["inputs"]["weather"] = "nonexistent"
    else:
        pack["cases"][2]["inputs"]["window"] = ["2027-09-01", "2027-05-15"]
    with pytest.raises(ValidationError):
        validate(AcceptancePack, pack)


def test_explicit_daily_expansion_is_inclusive_and_never_fills_unknown_dates():
    pack = validate(AcceptancePack, read("cases.v1.json"))
    days = expand_recipe(pack.weather_profiles["temperate_growth"])
    assert len(days) == 183
    assert days[0].date == date(2027, 4, 1) and days[-1].date == date(2027, 9, 30)
    assert all(d.liquid_rain_mm == 3 and d.wind_m_s is None for d in days)
    with pytest.raises(ValueError, match="explicit window required"):
        expand_recipe(pack.weather_profiles["cold_frozen"])
    with pytest.raises(ValueError, match="exceed explicit"):
        expand_recipe(
            pack.weather_profiles["temperate_growth"],
            DateWindow(start=date(2027, 1, 1), end=date(2027, 1, 2)),
        )


def test_expansion_respects_leap_year_and_bounds():
    recipe = validate(WeatherRecipe, read("cases.v1.json")["weather_profiles"]["cold_frozen"])
    days = expand_recipe(recipe, DateWindow(start=date(2028, 2, 28), end=date(2028, 3, 1)))
    assert [d.date.day for d in days] == [28, 29, 1]
    with pytest.raises(ValueError, match="2192"):
        expand_recipe(recipe, DateWindow(start=date(2000, 1, 1), end=date(2030, 1, 1)))


def test_generated_weather_matches_only_explicit_recipe_windows():
    generated = read("weather-fixtures.generated.json")
    pack = validate(AcceptancePack, read("cases.v1.json"))
    assert generated["kind"] == "synthetic_contract" and generated["not_empirical_evidence"]
    assert set(generated["records"]) == {"temperate_growth", "hot_dry", "warm_wet", "G03", "G04"}
    for name in ("temperate_growth", "hot_dry", "warm_wet"):
        assert generated["records"][name] == [
            d.model_dump(mode="json") for d in expand_recipe(pack.weather_profiles[name])
        ]


def test_empty_empirical_manifest_does_not_claim_validation():
    manifest = validate(EmpiricalManifest, read("empirical-sites.v1.json"))
    assert manifest.status == "insufficient_data" and not manifest.sites
    data = manifest.model_dump(mode="json")
    data["status"] = "acquired_not_evaluated"
    with pytest.raises(ValidationError):
        validate(EmpiricalManifest, data)


def test_rice_registry_does_not_change_legacy_crop_enum(example):
    from typing import get_args

    from field_shift.modules.rotations.planning_schemas import Crop

    entries = example["crop_registry"]["entries"]
    assert "Rice" in [e["crop_id"] for e in entries]
    assert "Rice" not in get_args(Crop)


def test_every_predicate_has_versioned_parameters_and_capability_bindings():
    from typing import get_args

    from field_shift_contracts.planning_v1.common import Capability

    pack = read("cases.v1.json")
    bindings = read("case-bindings.v1.json")
    by_id = {c["case_id"]: c for c in bindings["cases"]}
    assert len(by_id) == 24
    for case in pack["cases"]:
        binding = by_id[case["id"]]
        assert set(binding["parameters"]) <= set(bindings["parameters"])
        assert binding["parameters"]
        assert set(binding["required_capabilities"]) <= set(get_args(Capability))
        assert binding["domain_implementation_version"] is None
        assert not binding["agronomist_reviewed"]
        assert [p["text"] for p in binding["predicates"]] == case["expected_invariants"]
        assert [p["invariant_index"] for p in binding["predicates"]] == list(
            range(len(case["expected_invariants"]))
        )
        assert all(
            p["execution_status"] == "pending_domain_implementation" for p in binding["predicates"]
        )


def test_ownership_is_disjoint_and_migration_heads_have_common_parent():
    ownership = read("ownership.v1.json")
    assert ownership["integration_branch"] == "nazar-jobs"
    groups = {name: lane["paths"] for name, lane in ownership["lanes"].items()}
    groups["frozen"] = ownership["frozen_paths"]
    groups["integration"] = ownership["shared_integration_only"]

    def covers(prefix, path):
        return path == prefix or (prefix.endswith("/") and path.startswith(prefix))

    for name, paths in groups.items():
        for other_name, other_paths in groups.items():
            if name == other_name:
                continue
            for path in paths:
                assert not any(covers(path, p) or covers(p, path) for p in other_paths)
    parents = ownership["migration_parents"]
    assert (
        parents["0011_field_profiles"] == parents["0012_planning_runs"] == ["0010_precision_plans"]
    )
    assert set(parents["0013_merge_planning"]) == {"0011_field_profiles", "0012_planning_runs"}


def test_cross_farm_alternatives_and_duplicate_plans_are_rejected(example):
    comparison = example["comparison"]
    comparison.update(solver_status="feasible", alternatives=[example["alternative"]])
    model = PlanningContracts.model_fields["comparison"].annotation
    validate(model, comparison)
    duplicate = {**copy.deepcopy(example["alternative"]), "alternative_id": "another-name"}
    comparison["alternatives"].append(duplicate)
    with pytest.raises(ValidationError, match="equivalent alternative"):
        validate(model, comparison)
    comparison["alternatives"].pop()
    comparison["alternatives"][0]["assignments"][0]["field_id"] = "another-farm-field"
    with pytest.raises(ValidationError, match="all selected fields"):
        validate(model, comparison)


@pytest.mark.parametrize("season_count", [1, 2, 3])
def test_request_accepts_each_bounded_horizon_without_rewriting_it(example, season_count):
    data = example["request"]
    data["season_count"] = season_count
    parsed = validate(FarmPlanningRequest, data)
    assert parsed.season_count == season_count
    assert parsed.model_dump(mode="json") == data


@pytest.mark.parametrize("season_count", [0, 4, 6, "2"])
def test_request_horizon_does_not_expand_model_capacity(example, season_count):
    data = example["request"]
    data["season_count"] = season_count
    with pytest.raises(ValidationError):
        validate(FarmPlanningRequest, data)
