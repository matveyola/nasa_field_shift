"""M6C: one answer mixing a NASA-derived value, an ML estimate, a synthetic simulation result and a citation.

Each value keeps its provenance end to end, computed values are never observations, synthetic results say so
and carry their scenario, and a citation cannot vouch for a field-specific number it does not contain.
"""

import json

from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.ports import UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, say, tool_results
from agent_service.llm_agent import LLMAgent
from contracts.models import Fact
from evals.knowledge_cases import StubKnowledge, _record

from .conftest import context, request_body
from .test_trust_boundary import final

STRESS = _record("stub.stress.0001", "When soil water depletion exceeds readily available water, the water stress "
                                     "coefficient Ks falls below one and transpiration is reduced.")


def answer(transcript, tools):
    scores = tool_results(transcript)[1]["rotations"][0]["scores"]
    return say(
        "Вологість кореневої зони (SMAP): {{fact:f_sm_rootzone_2025_07}}. "
        "Оцінка моделі з вищою роздільністю: {{fact:f_sm_30m_mean_2025_07}}.\n\n"
        f"Для типових умов ризик дефіциту води rot_adaptive: {scores['water_risk']['cite_as']}.\n\n"
        "When depletion exceeds readily available water, the water stress coefficient Ks drops and transpiration "
        "falls {{cite:stub.stress.0001}}.\n\n"
        "Отже, вологість вашого поля 0,35 m³/m³ {{cite:stub.stress.0001}}.")  # a citation vouching for a field value


def test_one_answer_keeps_every_evidence_category_apart():
    script = [call(("get_field_facts", {"domains": ["water"], "fact_ids": []}),
                   ("compare_rotations", {"scenario": "typical", "rotation_ids": []}),
                   ("search_knowledge", {"query": "water stress", "topic": "any", "content": "any", "max_results": 3})),
              answer, answer]  # the bad sentence survives the repair turn, so it is redacted
    registry = IntegrationRegistry(UnavailableProvider("geo"), FixtureSimulationProvider(), geo_mode="off", sim_mode="fixture")
    body = request_body("poltava", "Покажи вологість ґрунту, порівняй сівозміни для типових умов і поясни водний стрес.")
    body["locale"] = "uk"
    msg = final(LLMAgent(ScriptedProvider(script), knowledge=StubKnowledge([STRESS]), integrations=registry), body)["message"]
    md = msg["blocks"][0]["markdown"]
    facts = {f["id"]: f for f in msg["facts"]}
    own = {f["id"]: f for f in context("poltava")["facts"]}

    # 1. NASA-derived value: provenance exactly as the context gave it.
    as_fact = lambda d: Fact.model_validate_json(json.dumps(d))  # noqa: E731
    assert as_fact(facts["f_sm_rootzone_2025_07"]) == as_fact(own["f_sm_rootzone_2025_07"])
    assert as_fact(facts["f_sm_30m_mean_2025_07"]) == as_fact(own["f_sm_30m_mean_2025_07"])
    assert facts["f_sm_rootzone_2025_07"]["provenance"]["source"]["product_id"] == "SPL4SMGP" and "**0,21 м³/м³**" in md
    # 2. ML estimate: kept as an estimate, rendered as one.
    assert facts["f_sm_30m_mean_2025_07"]["provenance"]["kind"] == "ml_estimate"
    assert "(неперевірена оцінка)" in md
    # 3. Synthetic simulation: computed, synthetic, named scenario; never an observation or a validated model.
    sims = [f for f in msg["facts"] if f["id"].startswith("sim.")]
    assert sims and all(f["provenance"]["data_status"] == "fixture" and "scenario=typical" in f["provenance"]["method"]
                        for f in sims)
    assert "розрахунок сівозміни, варіант «типові умови»" in md
    assert any(w["code"] == "fixture_data" for w in msg["warnings"])  # every value is demo: said once, under the answer
    # 4. The citation backs only what its passage says; the field number it was misused for is gone.
    assert [c["chunk_id"] for c in msg["citations"]] == ["stub.stress.0001"] and "[1]" in md
    assert "0,35" not in md and "0.35" not in md
    assert any("number_not_in_source" in (w.get("detail") or "") and "general_source_as_field_data" in w["detail"]
               for w in msg["warnings"])
    # Every number left in the answer is a server-rendered fact.
    assert set(facts) == {"f_sm_rootzone_2025_07", "f_sm_30m_mean_2025_07", *(f["id"] for f in sims)}
