"""Farmer constraint understanding (Task 05C, 2026-11-14): does the quoted evidence carry the proposed meaning?

Deterministic checks only. They show what the server refuses whatever a model proposes (cut quotes, dropped
negations, hedges, wishes filed as rules, unnamed or unknown crops, contradictions, history, silent replacement of a
confirmed condition) and that ordinary statements still pass. How a real model reads a farmer is not tested here.
"""

import json

import pytest
from agent_service.constraint_evidence import crop_catalogue, problem
from agent_service.dev_agent import _extract_constraints
from agent_service.llm.provider import ToolCall
from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent
from agent_service.prompts import SYSTEM_PROMPT
from agent_service.service import AgentService
from agent_service.tools import TurnState, default_registry, execute

from .conftest import as_request, request_body

CROPS = crop_catalogue()


def check(
    message: str, ctype: str, value, evidence: str, explicit: bool = True
) -> str | None:
    found = problem(
        ctype, value, evidence, message, explicit=explicit, known_crops=CROPS
    )
    return found[0] if found else None


# ------------------------------------------------------------------ statements that must stay valid


@pytest.mark.parametrize(
    ("message", "ctype", "value", "evidence"),
    [
        ("Не маю поливу.", "irrigation_available", False, "Не маю поливу"),
        ("Поливу немає.", "irrigation_available", False, "Поливу немає"),
        (
            "У нас є полив, і азоту хочу менше.",
            "irrigation_available",
            True,
            "У нас є полив",
        ),
        ("Ні, полив є.", "irrigation_available", True, "полив є"),
        ("Є полив. Не хочу соняшник.", "irrigation_available", True, "Є полив"),
        (
            "Полив є, але на це поле воду не подаватиму.",
            "irrigation_available",
            False,
            "на це поле воду не подаватиму",
        ),
        ("We have no irrigation.", "irrigation_available", False, "no irrigation"),
        ("We are dryland farmers.", "irrigation_available", False, "dryland farmers"),
        (
            "I said I had irrigation before, but now I don't.",
            "irrigation_available",
            False,
            "I said I had irrigation before, but now I don't",
        ),
        (
            "Не хочу садити соняшник.",
            "excluded_crops",
            ["sunflower"],
            "Не хочу садити соняшник",
        ),
        (
            "I prefer wheat but cannot grow sunflower.",
            "preferred_crops",
            ["winter_wheat"],
            "I prefer wheat",
        ),
        (
            "I prefer wheat but cannot grow sunflower.",
            "excluded_crops",
            ["sunflower"],
            "cannot grow sunflower",
        ),
        (
            "Хочу більше сої й кукурудзи.",
            "preferred_crops",
            ["soybean", "maize"],
            "Хочу більше сої й кукурудзи",
        ),
        (
            "Сівозміна максимум на 4 роки.",
            "rotation_length_years",
            4,
            "Сівозміна максимум на 4 роки",
        ),
        (
            "A rotation of four years at most.",
            "rotation_length_years",
            4,
            "A rotation of four years",
        ),
        (
            "Минулого року була кукурудза.",
            "previous_crop",
            "maize",
            "Минулого року була кукурудза",
        ),
        (
            "Поле на схилі, тому важливо зменшити ерозію.",
            "erosion_protection",
            True,
            "важливо зменшити ерозію",
        ),
        (
            "Хочу зменшити витрати на добрива.",
            "fertilizer_budget",
            "low",
            "зменшити витрати на добрива",
        ),
        # review round (2026-11-14): wording a negation-only rule had refused
        ("Без хімії, тільки органіка.", "organic_only", True, "Без хімії"),
        (
            "Хочемо вирощувати без пестицидів.",
            "organic_only",
            True,
            "вирощувати без пестицидів",
        ),
        (
            "Треба не допустити ерозії на схилі.",
            "erosion_protection",
            True,
            "не допустити ерозії на схилі",
        ),
        (
            "Irrigation is unavailable this year.",
            "irrigation_available",
            False,
            "Irrigation is unavailable this year",
        ),
        ("Полив відключили.", "irrigation_available", False, "Полив відключили"),
        (
            "Irrigation isn't available.",
            "irrigation_available",
            False,
            "Irrigation isn't available",
        ),
        (
            "There's no water for this field.",
            "irrigation_available",
            False,
            "There's no water for this field",
        ),
        (
            "Соняшник у нас був, більше не сіятиму.",
            "excluded_crops",
            ["sunflower"],
            "Соняшник у нас був, більше не сіятиму",
        ),
        (
            "We grew sunflower before but won't anymore.",
            "excluded_crops",
            ["sunflower"],
            "We grew sunflower before but won't anymore",
        ),
        (
            "Хочу 4-річну сівозміну.",
            "rotation_length_years",
            4,
            "Хочу 4-річну сівозміну",
        ),
        ("Чотирирічна сівозміна.", "rotation_length_years", 4, "Чотирирічна сівозміна"),
        (
            "We have irrigation, but no money for fertilizer.",
            "irrigation_available",
            True,
            "We have irrigation",
        ),
        ("Соняшник — ні.", "excluded_crops", ["sunflower"], "Соняшник — ні"),
        ("Виключіть соняшник.", "excluded_crops", ["sunflower"], "Виключіть соняшник"),
        (
            "Хочу пшеницю, а не соняшник.",
            "preferred_crops",
            ["winter_wheat"],
            "Хочу пшеницю",
        ),
    ],
)
def test_ordinary_statements_are_not_overblocked(message, ctype, value, evidence):
    assert check(message, ctype, value, evidence) is None


def test_inferences_need_no_quote_and_keep_only_the_cut_check():
    assert (
        check(
            "Поле на схилі.",
            "erosion_protection",
            True,
            "Поле на схилі",
            explicit=False,
        )
        is None
    )
    assert (
        check("Мабуть, є полив.", "irrigation_available", True, "", explicit=False)
        is None
    )
    assert (
        check(
            "Не маю поливу.", "irrigation_available", True, "маю поливу", explicit=False
        )
        == "evidence_cut"
    )


# ------------------------------------------------------------------ what is refused, and why


@pytest.mark.parametrize(
    ("message", "ctype", "value", "evidence", "category"),
    [
        # partial quotes that drop a negation or a qualifier
        ("Не маю поливу.", "irrigation_available", True, "маю поливу", "evidence_cut"),
        (
            "We don't have irrigation.",
            "irrigation_available",
            True,
            "have irrigation",
            "evidence_cut",
        ),
        (
            "Є полив лише на частині поля.",
            "irrigation_available",
            True,
            "Є полив",
            "evidence_cut",
        ),
        (
            "Не хочу садити соняшник.",
            "preferred_crops",
            ["sunflower"],
            "садити соняшник",
            "evidence_cut",
        ),
        (
            "Немає поливу.",
            "irrigation_available",
            True,
            "ема",
            "evidence_cut",
        ),  # inside a word
        # a condition in the same sentence
        (
            "Полив є, але на це поле воду не подаватиму.",
            "irrigation_available",
            True,
            "Полив є",
            "condition_omitted",
        ),
        (
            "We have irrigation, but not on this field.",
            "irrigation_available",
            True,
            "We have irrigation",
            "condition_omitted",
        ),
        # negation and meaning
        (
            "Поливу немає.",
            "irrigation_available",
            True,
            "Поливу немає",
            "polarity_mismatch",
        ),
        (
            "У нас є полив.",
            "irrigation_available",
            False,
            "У нас є полив",
            "polarity_mismatch",
        ),
        (
            "Не хочу соняшник.",
            "preferred_crops",
            ["sunflower"],
            "Не хочу соняшник",
            "polarity_mismatch",
        ),
        (
            "Сію соняшник.",
            "excluded_crops",
            ["sunflower"],
            "Сію соняшник",
            "polarity_mismatch",
        ),
        # too little of the farmer's words
        ("Поливу немає.", "irrigation_available", False, "немає", "evidence_too_short"),
        (
            "No, we don't.",
            "irrigation_available",
            False,
            "No, we don't",
            "evidence_too_short",
        ),
        # uncertainty, wishes and the past
        (
            "Мабуть, є полив.",
            "irrigation_available",
            True,
            "є полив",
            "uncertain_statement",
        ),
        (
            "Не знаю, чи є полив.",
            "irrigation_available",
            True,
            "є полив",
            "uncertain_statement",
        ),
        (
            "Maybe we have irrigation.",
            "irrigation_available",
            True,
            "we have irrigation",
            "uncertain_statement",
        ),
        (
            "Краще не сіяти соняшник.",
            "excluded_crops",
            ["sunflower"],
            "Краще не сіяти соняшник",
            "preference_not_restriction",
        ),
        (
            "I'd rather avoid sunflower.",
            "excluded_crops",
            ["sunflower"],
            "rather avoid sunflower",
            "preference_not_restriction",
        ),
        (
            "I said I had irrigation before, but now I don't.",
            "irrigation_available",
            True,
            "I had irrigation",
            "past_statement",
        ),
        # crops: only named ones, only from the catalogue
        (
            "Не хочу садити соняшник.",
            "excluded_crops",
            ["sunflower", "maize"],
            "Не хочу садити соняшник",
            "crop_not_mentioned",
        ),
        (
            "Хочу гречку.",
            "preferred_crops",
            ["buckwheat"],
            "Хочу гречку",
            "unsupported_crop",
        ),
        (
            "I like peanuts.",
            "preferred_crops",
            ["winter_pea"],
            "I like peanuts",
            "crop_not_mentioned",
        ),  # not "pea"
        # numbers
        (
            "Сівозміна максимум на 4 роки.",
            "rotation_length_years",
            5,
            "Сівозміна максимум на 4 роки",
            "value_not_in_evidence",
        ),
        # the same message says the opposite
        (
            "Поливу немає. Хоча є полив на частині поля.",
            "irrigation_available",
            False,
            "Поливу немає",
            "conflict",
        ),
        ("Є полив. Поливу немає.", "irrigation_available", True, "Є полив", "conflict"),
        (
            "Є полив. На південному полі поливу немає.",
            "irrigation_available",
            True,
            "Є полив",
            "conflict",
        ),
        # review round (2026-11-14): readings a negation-only rule had let through
        (
            "Не маю нічого проти соняшника.",
            "excluded_crops",
            ["sunflower"],
            "Не маю нічого проти соняшника",
            "polarity_mismatch",
        ),
        (
            "I don't mind sunflower.",
            "preferred_crops",
            ["sunflower"],
            "I don't mind sunflower",
            "uncertain_statement",
        ),
        (
            "Полив відключили.",
            "irrigation_available",
            True,
            "Полив відключили",
            "polarity_mismatch",
        ),
        (
            "Ми не органічне господарство.",
            "organic_only",
            True,
            "Ми не органічне господарство",
            "polarity_mismatch",
        ),
        (
            "We used to irrigate.",
            "irrigation_available",
            True,
            "We used to irrigate",
            "past_statement",
        ),
        (
            "I have irrigation only on two fields.",
            "irrigation_available",
            True,
            "I have irrigation",
            "evidence_cut",
        ),
    ],
)
def test_unsupported_readings_are_refused_with_a_reason(
    message, ctype, value, evidence, category
):
    assert check(message, ctype, value, evidence) == category


def test_a_crop_already_in_the_fields_history_may_be_named():
    known = crop_catalogue(["spring_barley"])
    assert (
        problem(
            "previous_crop",
            "spring_barley",
            "minulogo roku spring barley",
            "minulogo roku spring barley",
            explicit=True,
            known_crops=known,
        )
        is None
    )


# ------------------------------------------------------------------ the tool in a turn


def state_for(
    text: str, field: str = "kansas", history: list | None = None
) -> TurnState:
    body = request_body(field, text)
    if history is not None:
        body["history"] = history
    return TurnState(request=as_request(body), turn_id="turn_test")


def prop(
    ctype: str,
    value,
    evidence: str = "",
    basis: str = "explicit",
    confidence: float = 1,
) -> dict:
    return {
        "type": ctype,
        "value": value,
        "basis": basis,
        "evidence": evidence,
        "confidence": confidence,
    }


async def propose(state: TurnState, *proposals: dict) -> dict:
    result, _ = await execute(
        ToolCall("c", "propose_constraints", {"constraints": list(proposals)}),
        state,
        default_registry(with_knowledge=False),
        f"c{len(state.traces)}",
        5,
    )
    return json.loads(result.content)


@pytest.mark.anyio
async def test_history_cannot_supply_a_crop_exclusion():
    history = [{"role": "user", "text": "Не хочу садити соняшник."}]
    state = state_for("Що порадиш на наступний рік?", history=history)
    body = await propose(
        state, prop("excluded_crops", ["sunflower"], "Не хочу садити соняшник")
    )
    assert [r["category"] for r in body["rejected"]] == [
        "evidence_not_found"
    ] and not state.constraints


@pytest.mark.anyio
async def test_a_contradiction_in_the_message_blocks_the_type_for_the_whole_turn():
    state = state_for("Поливу немає. Хоча є полив на частині поля.")
    first = await propose(state, prop("irrigation_available", False, "Поливу немає"))
    assert first["rejected"][0]["category"] == "conflict"
    later = await propose(
        state, prop("irrigation_available", True, "є полив на частині поля")
    )
    assert (
        later["rejected"][0]["category"] == "conflict"
        and "irrigation_available" not in state.constraints
    )


@pytest.mark.anyio
async def test_the_same_crop_wanted_and_ruled_out_keeps_neither():
    state = state_for("Хочу соняшник. Але соняшник не сіятиму.")
    body = await propose(
        state,
        prop("preferred_crops", ["sunflower"], "Хочу соняшник"),
        prop("excluded_crops", ["sunflower"], "соняшник не сіятиму"),
    )
    assert body["accepted"] == [] and body["rejected"][-1]["category"] == "conflict"
    assert not state.constraints


@pytest.mark.anyio
async def test_an_inference_never_replaces_a_confirmed_condition():
    state = state_for(
        "Здається, тепер можна поливати.", field="poltava"
    )  # poltava: no irrigation, confirmed
    body = await propose(
        state, prop("irrigation_available", True, basis="inferred", confidence=0.6)
    )
    assert (
        body["rejected"][0]["category"] == "confirmed_by_farmer"
        and not state.constraints
    )


@pytest.mark.anyio
async def test_an_explicit_change_to_a_confirmed_condition_is_shown_never_silent():
    final_events = [
        e
        async for e in AgentService(
            LLMAgent(
                ScriptedProvider(
                    [
                        call(
                            (
                                "propose_constraints",
                                {
                                    "constraints": [
                                        prop(
                                            "irrigation_available",
                                            True,
                                            "тепер у нас є полив",
                                        )
                                    ]
                                },
                            )
                        ),
                        say(
                            "Раніше ви підтвердили «без поливу». Пропоную змінити на «полив є»; це запрацює, коли ви підтвердите."
                        ),
                    ]
                )
            ),
            10,
        ).stream(
            as_request(request_body("poltava", "Відтепер так: тепер у нас є полив."))
        )
    ]
    message = final_events[-1].message
    [proposed] = message.constraints_proposed
    assert (proposed.value, proposed.status, proposed.source) == (
        True,
        "proposed",
        "farmer_text",
    )
    assert any(
        "replace one you confirmed" in (w.detail or "") for w in message.warnings
    )


@pytest.mark.anyio
async def test_an_inferred_condition_stays_marked_and_asks_for_confirmation():
    state = state_for("Поле на схилі.")
    body = await propose(
        state,
        prop(
            "erosion_protection",
            True,
            "Поле на схилі",
            basis="inferred",
            confidence=0.6,
        ),
    )
    constraint = state.constraints["erosion_protection"]
    assert body["accepted"][0]["basis"] == "inferred"
    assert (constraint.source, constraint.confidence, constraint.status) == (
        "agent_inferred",
        0.6,
        "proposed",
    )


@pytest.mark.anyio
async def test_a_rejection_names_what_to_do_instead():
    state = state_for("Краще не сіяти соняшник.")
    body = await propose(
        state, prop("excluded_crops", ["sunflower"], "Краще не сіяти соняшник")
    )
    reason = body["rejected"][0]["reason"]
    assert "inferred" in reason and "ask" in reason
    accepted = await propose(
        state,
        prop(
            "excluded_crops",
            ["sunflower"],
            "Краще не сіяти соняшник",
            basis="inferred",
            confidence=0.5,
        ),
    )
    assert accepted["accepted"][0]["basis"] == "inferred"


# ------------------------------------------------------------------ the rules agent and the prompt


def test_the_rules_agent_reads_ne_mayu_polyvu_as_no_irrigation():
    [constraint] = _extract_constraints("Не маю поливу, хочу порівняти сівозміни.")
    assert (constraint.type, constraint.value, constraint.evidence) == (
        "irrigation_available",
        False,
        "Не маю поливу",
    )
    assert (
        _extract_constraints("Поливу немає. Хоча є полив на частині поля.") == []
    )  # contradiction: nothing kept


def test_the_prompt_and_tool_say_how_to_quote_and_when_to_ask():
    assert "including any negation or condition" in SYSTEM_PROMPT
    assert (
        "Keep a wish" in SYSTEM_PROMPT
        and "replace a condition the farmer confirmed" in SYSTEM_PROMPT
    )
    spec = default_registry(with_knowledge=False)[
        "propose_constraints"
    ].spec.description
    assert (
        "Earlier messages are never evidence" in spec
        and "winter_wheat" in spec
        and "buckwheat" not in spec
    )
