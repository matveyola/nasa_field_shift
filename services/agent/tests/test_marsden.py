"""The Marsden rotation API behind the team's simulation contract: only what Marsden computes, labelled as it labels it."""

import json

import httpx2
import pytest

from agent_service.integrations.marsden import MarsdenSimulationProvider
from agent_service.integrations.normalize import check_comparison
from agent_service.integrations.ports import IntegrationFailure
from contracts.integration import ComparisonRequest
from contracts.models import CompareRotationsRequest

CATALOG = {
    "scenarios": [{"id": "baseline"}, {"id": "drought"}, {"id": "heat"}],
    "rotations": [{"id": "marsden-a", "crop_ids": ["maize_grain", "sunflower"]},
                  {"id": "marsden-b", "crop_ids": ["sunflower", "maize_grain"]}],
}


def _metric(value, method):
    return {"value_pct": value, "method": method, "accuracy": "not_validated_for_field" if value is not None else "unavailable",
            "validation_error_pct_points": None}


def _rotation(rid, crops, water, loss):
    return {"rotation": {"id": rid, "crop_ids": crops}, "water_shortfall": _metric(water, "formula"),
            "modeled_yield_loss_due_to_water": _metric(loss, "model"), "yield_risk": _metric(None, "unavailable"),
            "fertilizer": {"n_kg_ha": None, "p2o5_kg_ha": None, "k2o_kg_ha": None, "method": "unavailable"}}


def _assessment(body):
    years = {"baseline": (2021, 2022), "drought": (2022, 2023), "heat": (2020, 2021)}
    return {"results": [{
        "scenario": {"id": s, "analog_year": years[s][1], "replay_years": list(years[s])},
        "rotations": [_rotation("marsden-a", ["maize_grain", "sunflower"], 26.1, 28.2),
                      _rotation("marsden-b", ["sunflower", "maize_grain"], 30.9, 34.3)],
    } for s in body["scenarios"]]}


def marsden(sent: list | None = None) -> MarsdenSimulationProvider:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/rotations/assessment/catalog":
            return httpx2.Response(200, json=CATALOG)
        if request.url.path == "/openapi.json":
            return httpx2.Response(200, json={"info": {"version": "2.2.0"}})
        body = json.loads(request.content)
        if sent is not None:
            sent.append(body)
        if len(body.get("rotation_ids", [])) != 2:  # like Marsden: exactly two plans
            return httpx2.Response(422, json={"detail": "two rotations"})
        return httpx2.Response(200, json=_assessment(body))
    return MarsdenSimulationProvider("http://marsden.test", {"demo-marsden-01": "marsden_demo"},
                                     transport=httpx2.MockTransport(handler))


def ask(scenario="typical", field="demo-marsden-01", rotation_ids=()):
    return ComparisonRequest(contract_version="0.1", request_id=f"req_{scenario}", idempotency_key="idem_1",
                             comparison=CompareRotationsRequest(field_id=field, field_context_revision=1, scenario=scenario,
                                                                rotation_ids=list(rotation_ids)))


@pytest.mark.anyio
async def test_capabilities_offer_only_the_archive_scenarios_marsden_has():
    caps = await marsden().capabilities("cap_1")
    assert caps.service.mode == "live" and "Marsden API 2.2.0" in caps.service.implementation
    assert caps.simulation.scenarios == ["typical", "driest_recent_year", "hottest_recent_year"]


@pytest.mark.anyio
async def test_only_what_marsden_computes_reaches_the_agent_labelled_as_marsden_labels_it():
    sent = []
    p = marsden(sent)
    req = ask("driest_recent_year")
    resp = await p.compare(req)
    assert sent == [{"field_id": "marsden_demo", "scenarios": ["drought"], "rotation_ids": ["marsden-a", "marsden-b"]}]
    checked = check_comparison(resp, req)  # the agent's own boundary checks pass
    a = resp.comparison.rotations[0]
    assert [s.crop_id for s in a.sequence] == ["maize", "sunflower"]
    assert a.scores.water_risk.value == 26.1 and a.scores.water_risk.provenance.kind == "deterministic_calc"
    assert "share of crop water need not met" in a.scores.water_risk.provenance.method
    loss = a.scores.yield_loss_water
    assert loss.value == 28.2 and loss.provenance.kind == "ml_estimate" and loss.provenance.model.validation is None
    # fertilizer and yield risk are null in Marsden: absent here, never zero
    assert a.scores.n_input is None and a.scores.yield_risk is None and a.scores.soil_cover_days is None
    # the scenario is the archive season Marsden picked, said in the method; the agent keeps scenario= in its facts
    assert "driest season 2023" in a.scores.water_risk.provenance.method
    assert all("scenario=driest_recent_year" in f.provenance.method for f in checked.facts.values())
    notes = " ".join(w.detail for w in resp.warnings)
    assert "Fertilizer rates are not computed" in notes and "not a recommendation" in notes
    assert "does not use the farmer's conditions" in notes  # even when none were sent: no "we will take them into account"


@pytest.mark.anyio
async def test_no_substitutes_other_fields_and_other_scenarios_are_refused_not_replaced():
    p = marsden()
    assert (await p.compare(ask(field="demo-poltava-01"))).failure.code == "insufficient_data"
    for scenario in ("drought_2024", "heat_plus_2_5c"):  # never answered with the driest / hottest archive year
        assert (await p.compare(ask(scenario))).failure.code == "unsupported_scenario"


@pytest.mark.anyio
async def test_one_requested_rotation_comes_back_alone_and_results_can_be_fetched_by_id():
    p = marsden()
    req = ask(rotation_ids=["marsden-b"])
    resp = await p.compare(req)
    check_comparison(resp, req)  # would refuse rotations that were not requested
    assert [r.rotation_id for r in resp.comparison.rotations] == ["marsden-b"]
    again = await p.result(resp.result_id, "req_host")
    assert again.request_id == "req_host" and again.comparison == resp.comparison
    with pytest.raises(IntegrationFailure) as e:
        await p.result("cmp_unknown", "req_host")
    assert e.value.category == "not_found"
