"""Citations, search_knowledge integration, retrieval fallbacks (real FAO-56 index, offline)."""

import asyncio
import json

import numpy as np
import pytest

from agent_service.config import Settings
from agent_service.grounding import check
from agent_service.knowledge.retrieve import KnowledgeBase
from agent_service.llm.scripted import ScriptedProvider, call, say, tool_results
from agent_service.llm_agent import LLMAgent, LoopLimits
from agent_service.service import AgentService
from evals.harness import INDEX_PATH

from .conftest import as_request, make_client, parse_sse, request_body, schema_validator

pytestmark = pytest.mark.skipif(not INDEX_PATH.exists(), reason="index not built: python -m agent_service.knowledge build")

MAIZE_ROW = "Crop: Maize, Field (grain) (field corn)"


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase(INDEX_PATH, mode="lexical", load_models=False)


@pytest.fixture(scope="module")
def maize(kb):
    return next(c for c in kb.chunks.values() if c.table == "Table 12" and MAIZE_ROW in c.text)


def grounded(text, kb, evidence):
    return check(text, retrieved={}, known_ids=set(), farmer_text="", simulation_ran=False, any_live_fact=False,
                 lang="en", evidence=evidence, index_has=lambda cid: cid in kb.chunks)


# ------------------------------------------------------------------ citation rules


@pytest.mark.parametrize("sentence, expected", [
    ("For field maize the mid-season Kc is 1.20 {cite}.", set()),
    ("Для кукурудзи на зерно Kc у середині сезону 1,20 {cite}.", set()),
    ("For field maize the initial Kc is 1.20 {cite}.", {"wrong_table_column"}),
    ("For field maize the mid-season Kc is 1.15 {cite}.", {"wrong_table_row"}),
    ("For field maize the mid-season Kc is 1.42 {cite}.", {"number_not_in_source"}),
    ("On your field the Kc mid of maize is 1.20 {cite}.", {"general_source_as_field_data"}),
    ("Wind and sunshine dry tomato leaves fast {cite}.", {"citation_unrelated"}),
    ("FAO-56 tabulates crop coefficients by stage in 1998 {cite}.", set()),  # product names and years are not claims
])
def test_cited_sentence_rules(kb, maize, sentence, expected):
    g = grounded(sentence.format(cite="{{cite:" + maize.chunk_id + "}}"), kb, {maize.chunk_id: maize})
    assert {v.category for v in g.violations} == expected


def test_citation_ids_must_be_real_and_retrieved(kb, maize):
    other = next(cid for cid in kb.chunks if cid != maize.chunk_id)
    g = grounded("A {{cite:fao56-ch06.invented.0000000000}}. B {{cite:" + other + "}}.", kb, {maize.chunk_id: maize})
    assert [v.category for v in g.violations] == ["fabricated_citation", "citation_not_retrieved"]
    assert "{{" not in g.markdown and g.citations == []


def test_quotes_are_verified_whitespace_insensitively_and_returned_verbatim(kb, maize):
    ok = grounded('Maize mid Kc {{cite:' + maize.chunk_id + '|"kc   mid:  1.20"}}.', kb, {maize.chunk_id: maize})
    assert ok.violations == [] and ok.citations[0].quote == "Kc mid: 1.20"  # the source's characters, not the model's
    bad = grounded('Maize mid Kc {{cite:' + maize.chunk_id + '|"Kc mid: 1.25"}}.', kb, {maize.chunk_id: maize})
    assert [v.category for v in bad.violations] == ["fabricated_quote"]


def test_model_urls_are_rejected(kb, maize):
    g = grounded("See www.fao.org/x and https://evil.example/a {{cite:" + maize.chunk_id + "}}.", kb, {maize.chunk_id: maize})
    assert [v.category for v in g.violations] == ["unverified_url", "unverified_url"]


def test_citation_object_is_contract_valid_and_attributed(kb, maize):
    g = grounded("Field maize mid-season Kc is 1.20 {{cite:" + maize.chunk_id + "}}.", kb, {maize.chunk_id: maize})
    (c,) = g.citations
    schema_validator("Citation").validate(json.loads(c.model_dump_json()))
    assert (c.publisher, c.year, c.locator.chapter, c.locator.table) == ("FAO", 1998, "6", "Table 12")
    assert str(c.url) == maize.url and c.quote in maize.text and "FAO" in c.licence and "1.20" in c.quote
    assert g.markdown == "Field maize mid-season Kc is 1.20 [1]."


def test_same_chunk_cited_twice_is_one_citation(kb, maize):
    token = "{{cite:" + maize.chunk_id + "}}"
    g = grounded(f"Maize mid Kc is 1.20 {token}. Sugar beet mid Kc is also 1.20 {token}.", kb, {maize.chunk_id: maize})
    assert len(g.citations) == 1 and g.markdown.count("[1]") == 2


# ------------------------------------------------------------------ retrieval fallbacks


class _Broken:
    def __init__(self, kb):
        self.model_id, self.revision, self.dtype = kb.meta["embedding_model"], kb.meta["embedding_revision"], "float32"

    def encode(self, texts):
        raise RuntimeError("model crashed")

    def score(self, query, passages):
        raise RuntimeError("model crashed")


class _FirstRow:
    def __init__(self, kb):
        self.model_id, self.revision, self.kb = kb.meta["embedding_model"], kb.meta["embedding_revision"], kb

    def encode(self, texts):
        return np.asarray([self.kb.matrix[0]], dtype=np.float32)


@pytest.mark.skipif(not INDEX_PATH.exists(), reason="no index")
def test_dense_and_reranker_failures_degrade_and_say_so():
    base = KnowledgeBase(INDEX_PATH, mode="lexical", load_models=False)
    if base.matrix is None:
        pytest.skip("index built without embeddings")
    kb = KnowledgeBase(INDEX_PATH, mode="hybrid_rerank", embedder=_Broken(base), reranker=_Broken(base), load_models=False)
    result = kb.search("Kc mid maize", k=3)
    assert result.mode == "lexical" and "dense retrieval failed" in result.degraded and result.hits
    kb2 = KnowledgeBase(INDEX_PATH, mode="hybrid_rerank", embedder=_FirstRow(base), reranker=_Broken(base), load_models=False)
    result2 = kb2.search("Kc mid maize", k=3)
    assert result2.mode == "hybrid" and "reranker failed" in result2.degraded and result2.hits


# ------------------------------------------------------------------ agent integration


def _search(query="Kc mid maize Table 12", content="tables"):
    return call(("search_knowledge", {"query": query, "topic": "crop_coefficients", "content": content, "max_results": 8}))


def _cite_maize(transcript, tools):
    for item in tool_results(transcript)[-1]["results"]:
        if MAIZE_ROW in item["text"]:
            return say("Field maize has a mid-season Kc of 1.20 {{cite:" + item["ref"] + "}}.")
    return say("No source found.")


def test_sse_stream_carries_search_events_and_valid_citations(kb):
    agent = LLMAgent(ScriptedProvider([_search(), _cite_maize]), knowledge=kb)
    with make_client(agent=agent) as client:
        assert client.get("/health/ready").json()["knowledge"]["mode"] == "lexical"
        with client.stream("POST", "/v1/agent/stream", json=request_body("kansas", "Kc mid maize?")) as response:
            frames = parse_sse("".join(response.iter_text()))
    names = [(n, d.get("tool")) for n, _i, d in frames]
    assert ("tool.started", "search_knowledge") in names
    for _n, _i, data in frames:
        schema_validator("AgentEvent").validate(data)
    message = frames[-1][2]["message"]
    assert len(message["citations"]) == 1 and message["blocks"][0]["markdown"].endswith("1.20 [1].")


def test_readiness_reports_missing_index():
    settings = Settings(environment="test", agent="scripted-llm", rag_mode="hybrid_rerank", rag_index_path="nope.sqlite")
    with make_client(settings) as client:
        assert client.get("/health/ready").json()["knowledge"] == {"mode": "unavailable"}


@pytest.mark.anyio
async def test_evidence_is_per_turn_under_concurrency(kb):
    """Two concurrent turns on one agent: each may cite only what its own search returned."""
    def leaky(transcript, tools):
        mine = tool_results(transcript)[-1]["results"][0]["ref"]
        others = [cid for cid in kb.chunks if cid != mine][:1]
        return say(f"Point {{{{cite:{mine}}}}}. Other {{{{cite:{others[0]}}}}}.")

    agent = LLMAgent(ScriptedProvider(lambda t, tools: _search("Kc maize") if not tool_results(t) else leaky(t, tools)),
                     LoopLimits(max_repairs=0), knowledge=kb)
    service = AgentService(agent, 10)

    async def one():
        return [e async for e in service.stream(as_request(request_body("kansas", "Kc?")))][-1].message

    for message in await asyncio.gather(one(), one()):
        assert len(message.citations) <= 1  # the foreign chunk was never retrieved in that turn


@pytest.mark.anyio
async def test_cancellation_during_search_stops_the_turn():
    from evals.knowledge_cases import StubKnowledge, _record

    slow = StubKnowledge([_record("stub.slow.0002", "Slow.")], delay_s=2.0)
    agent = LLMAgent(ScriptedProvider([_search()]), knowledge=slow)
    events = []

    async def consume():
        async for e in AgentService(agent, 60).stream(as_request(request_body("kansas", "Kc?"))):
            events.append(e.event)

    task = asyncio.create_task(consume())
    for _ in range(100):
        if "tool.started" in events:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["turn.started", "tool.started"]  # no result, no terminal event after cancellation


# ------------------------------------------------------------------ the demo's FAO water-stress question (Task 11)

@pytest.mark.parametrize("question", ["Що FAO-56 говорить про водний стрес?", "What does FAO-56 say about water stress?"])
def test_water_stress_question_retrieves_and_cites_chapter_8_without_a_relevance_warning(kb, question):
    """Before: the corpus name ("FAO-56" -> "fao", "56") outranked the topic and «водний» was untranslated, so a
    Chapter 6 Kc worked example was quoted and the answer was (correctly) flagged as weakly related."""
    top = kb.search(question, k=1, topic=None, content="text").hits[0].chunk
    assert top.chapter == "8" and "stress" in (top.section + top.text).lower()
    from agent_service.llm.scripted import demo_script
    with make_client(agent=LLMAgent(ScriptedProvider(demo_script), knowledge=kb)) as client:
        message = parse_sse(client.post("/v1/agent/stream", json=request_body("poltava", question)).text)[-1][2]["message"]
    assert len(message["citations"]) == 2
    assert all(c["chunk_id"].startswith("fao56-ch08.") and "water stress" in c["quote"].lower().replace("stressed", "stress")
               for c in message["citations"])
    assert not any(w["code"] == "low_confidence" for w in message["warnings"])
