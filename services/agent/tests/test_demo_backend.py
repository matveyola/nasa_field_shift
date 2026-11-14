"""Task 11: the scripted demo backend serves every demo workflow through the real tool registry.

FAO-56 retrieval: tests/test_rag.py (real index). Scenarios: tests/test_scenario_integrity.py.
"""

from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.ports import UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, demo_script
from agent_service.llm_agent import LLMAgent

from .conftest import make_client, parse_sse, request_body


def turn(text: str, field: str = "poltava", simulation: bool = True):
    sim = FixtureSimulationProvider() if simulation else UnavailableProvider("simulation")
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture" if simulation else "off")
    body = request_body(field, text)
    body["locale"] = "uk"
    with make_client(agent=LLMAgent(ScriptedProvider(demo_script), integrations=registry)) as client:
        frames = parse_sse(client.post("/v1/agent/stream", json=body).text)
    tools = [f[2]["tool"] for f in frames if f[0] == "tool.started"]
    return tools, frames[-1][2]["message"]


def test_a_standalone_condition_is_proposed_not_saved():
    tools, msg = turn("Поливу немає, хочу зменшити витрати на добрива.", field="mykolaiv")
    assert tools == ["propose_constraints"]
    assert [(c["id"], c["status"], c["evidence"]) for c in msg["constraints_proposed"]] == [
        ("k_irrigation_available", "proposed", "Поливу немає"), ("k_fertilizer_budget", "proposed", "зменшити витрати на добрива")]
    assert "не збережеться, доки ви не підтвердите" in msg["blocks"][0]["markdown"]


def test_soil_moisture_reads_water_facts_and_proposes_the_published_layer():
    tools, msg = turn("Покажи вологість ґрунту.")
    assert tools == ["get_field_facts", "propose_ui_actions"]
    (action,) = msg["ui_actions"]
    assert action["action"]["layer_ids"] == ["l_smap_rootzone"] and action["trigger"] == "auto"
    md = msg["blocks"][0]["markdown"]
    assert "**0,21 м³/м³**\n" in md and "**0,19 м³/м³ ± 0,05** (неперевірена оцінка)" in md  # satellite plain, estimate marked
    assert "fixture_data" in {w["code"] for w in msg["warnings"]}  # all values are demo: said once, under the answer
    assert any((f["provenance"].get("source") or {}).get("product_id") == "SPL4SMGP" for f in msg["facts"])  # exact product kept


def test_a_rotation_question_without_a_simulation_service_says_so():
    tools, msg = turn("Порівняй сівозміни для мого поля.", simulation=False)
    assert "compare_rotations" not in tools and not msg["ui_actions"]
    assert "не підключено" in msg["blocks"][0]["markdown"]
