"""Record real agent-service SSE responses for the Playwright tests.

The e2e tests must not need a running backend, an LLM key or the network, so they replay what
the real service produced for the demo's fixture fields. Run from field-shift-agent/:

    PYTHONPATH=. uv run python frontend/e2e/record_fixtures.py

Rules-based answers come from the deterministic agent; the FAO-56 answer from the scripted LLM
loop with the lexical knowledge index (data/knowledge/index must be built).
"""

import json
from pathlib import Path

from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.ports import UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.knowledge.retrieve import KnowledgeBase
from agent_service.llm.scripted import ScriptedProvider, demo_script
from agent_service.llm_agent import LLMAgent
from evals.harness import INDEX_PATH
from tests.conftest import make_client, request_body

OUT = Path(__file__).parent / "fixtures"

RULES = {
    "field_uk": "Що відомо про моє поле?",
    "constraints_uk": "Поливу немає. Хочу менше витрачати на добрива.",
    "map_uk": "Покажи, скільки вологи в ґрунті.",
    "simulation_uk": "Порахуй урожайність кукурудзи під час посухи.",
}
KNOWLEDGE = {"fao_uk": "Що таке водний стрес у рослин?"}
# The scripted loop with the in-process fixture simulation (M5): a synthetic comparison and its result body.
SIMULATION = {"compare_uk": "Що посіяти далі? Порівняй сівозміни."}  # the rotation suggestion's own words


def record(client, name: str, text: str) -> None:
    body = request_body("poltava", text)  # the demo's first field, as the widget sends it (revision 3)
    body["locale"] = "uk"
    with client.stream("POST", "/v1/agent/stream", json=body) as response:
        raw = "".join(response.iter_text())
    assert "event: turn.completed" in raw, (name, raw[-300:])
    (OUT / f"{name}.sse").write_text(raw, encoding="utf-8", newline="\n")
    print(f"{name}: {len(raw)} bytes")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with make_client() as client:  # deterministic agent
        for name, text in RULES.items():
            record(client, name, text)
    kb = KnowledgeBase(INDEX_PATH, mode="lexical", load_models=False)
    with make_client(agent=LLMAgent(ScriptedProvider(demo_script), knowledge=kb)) as client:
        for name, text in KNOWLEDGE.items():
            record(client, name, text)
    sim = FixtureSimulationProvider(mode="stub")  # the demo host fetches results from the contract stub
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    with make_client(agent=LLMAgent(ScriptedProvider(demo_script), knowledge=kb, integrations=registry)) as client:
        for name, text in SIMULATION.items():
            record(client, name, text)
            [result] = sim.results.values()
            (OUT / f"{name}.result.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    (OUT / "index.json").write_text(json.dumps({**RULES, **KNOWLEDGE, **SIMULATION}, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
