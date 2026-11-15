"""calculate_farm_budget (Task 05A, 2026-11-14): exact arithmetic on figures the farmer wrote in THIS message.

The model sends an operation and quotes, never numbers. The server finds each quote in the current message, reads the
number and unit from the farmer's own text, calculates with decimals and returns facts the answer must cite. Mocked
model only: these tests show the server-side guarantees, not how a real model chooses to call the tool.
"""

import json
from decimal import Decimal

import pytest

from agent_service import budget
from agent_service.grounding import format_money, format_quantity, render_fact
from agent_service.llm.provider import ToolCall
from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent
from agent_service.service import AgentService
from agent_service.tools import MAX_CALCULATIONS_PER_TURN, TurnState, default_registry, execute, never_runs
from contracts.models import Quantity, TurnCompletedEvent

from .conftest import as_request, request_body, schema_validator

NBSP = " "


def calc(message: str, operation: str, first: tuple[str, str], second: tuple[str, str]) -> budget.Result:
    return budget.calculate(operation, budget.read_operand(*first, message), budget.read_operand(*second, message))


def refusal(message: str, operation: str, first: tuple[str, str], second: tuple[str, str]) -> str:
    with pytest.raises(budget.BudgetRefused) as e:
        calc(message, operation, first, second)
    return e.value.category


# ------------------------------------------------------------------ the arithmetic


@pytest.mark.parametrize(("message", "first", "second"), [
    ("Маю 300 000 грн на 40 га. Скільки виходить на гектар?", ("300 000 грн", "UAH"), ("40 га", "ha")),
    ("Маю 300 тис. грн на 40 гектарів.", ("300 тис. грн", "UAH"), ("40 гектарів", "ha")),
    ("I have 300,000 UAH for 40 hectares. How much per hectare?", ("300,000 UAH", "UAH"), ("40 hectares", "ha")),
    ("I have 300000 UAH for 40 ha.", ("300000 UAH", "UAH"), ("40 ha", "ha")),
])
def test_budget_per_hectare_in_ukrainian_and_english(message, first, second):
    result = calc(message, "per_hectare", first, second)
    assert (result.value, result.unit) == (Decimal("7500.00"), "UAH/ha")
    assert result.expression == "300000 UAH / 40 ha = 7500.00 UAH/ha"


def test_cost_per_hectare_times_area_keeps_decimals_exact():
    result = calc("Добрива коштують 5 500 грн/га, а сію на 37,5 га.", "total_for_area",
                  ("5 500 грн/га", "UAH/ha"), ("37,5 га", "ha"))
    assert (result.value, result.unit) == (Decimal("206250.00"), "UAH")
    result = calc("Seed costs $120.40 per hectare on 12.5 ha.", "total_for_area", ("$120.40 per hectare", "USD/ha"),
                  ("12.5 ha", "ha"))
    assert (result.value, result.unit) == (Decimal("1505.00"), "USD")


def test_revenue_minus_expenses_and_a_loss_stays_negative():
    result = calc("Revenue was $120,500.50 and expenses $98,000.", "difference", ("$120,500.50", "USD"),
                  ("$98,000", "USD"))
    assert (result.value, result.unit) == (Decimal("22500.50"), "USD")
    loss = calc("Виручка 400 000 грн, витрати 450 000 грн.", "difference", ("400 000 грн", "UAH"), ("450 000 грн", "UAH"))
    assert loss.value == Decimal("-50000.00")


def test_rounding_is_explicit_half_up_to_kopecks():
    assert calc("Маю 100 000 грн на 3 га.", "per_hectare", ("100 000 грн", "UAH"), ("3 га", "ha")).value == Decimal("33333.33")
    assert calc("Маю 1 грн на 8 га.", "per_hectare", ("1 грн", "UAH"), ("8 га", "ha")).value == Decimal("0.13")  # 0.125


def test_two_per_hectare_costs_can_be_compared():
    result = calc("Варіант А коштує 5 000 грн/га, варіант Б 6 200 грн/га.", "difference",
                  ("6 200 грн/га", "UAH/ha"), ("5 000 грн/га", "UAH/ha"))
    assert (result.value, result.unit) == (Decimal("1200.00"), "UAH/ha")


# ------------------------------------------------------------------ refusals instead of guesses


@pytest.mark.parametrize(("message", "first", "category"), [
    ("Маю 300,000 грн на 40 га.", ("300,000 грн", "UAH"), "ambiguous_number"),     # 300 thousand or 300.000?
    ("Budget 1.500 UAH for 3 ha.", ("1.500 UAH", "UAH"), "ambiguous_number"),
    ("Маю 0300 грн на 40 га.", ("0300 грн", "UAH"), "ambiguous_number"),
    ("Маю 300 000 € на 40 га.", ("300 000 €", "UAH"), "unsupported_currency"),
    ("Маю 300 000 на 40 га.", ("300 000", "UAH"), "missing_unit"),
    ("Маю 300 000 грн на 40 га.", ("300 000 грн", "USD"), "unit_mismatch"),        # the model may not relabel
    ("Маю 300 000 грн на 40 га.", ("00 грн", "UAH"), "partial_number"),            # cut-off number
    ("I have 300 thousand UAH for 40 ha.", ("300", "UAH"), "partial_number"),      # "thousand" left out
    ("Маю 300 000 грн на 40 га.", ("300 000 грн на 40 га", "UAH"), "one_number_per_quote"),
    ("Маю -300 000 грн на 40 га.", ("300 000 грн", "UAH"), "invalid_value"),
    ("I have 900000000000000 UAH for 40 ha.", ("900000000000000 UAH", "UAH"), "too_large"),
    ("I have 200000000000 UAH for 40 ha.", ("200000000000 UAH", "UAH"), "too_large"),
])
def test_unclear_amounts_are_refused_not_guessed(message, first, category):
    assert refusal(message, "per_hectare", first, ("40 га" if "га" in message else "40 ha", "ha")) == category


@pytest.mark.parametrize(("message", "second", "category"), [
    ("Маю 300 000 грн на 0 га.", ("0 га", "ha"), "zero_area"),
    ("Маю 300 000 грн на 40.", ("40", "ha"), "missing_unit"),
    ("Маю 300 000 грн на 40 соток.", ("40 соток", "ha"), "unsupported_unit"),
    ("Маю 300 000 грн на 2 000 000 га.", ("2 000 000 га", "ha"), "too_large"),
])
def test_unclear_areas_are_refused(message, second, category):
    assert refusal(message, "per_hectare", ("300 000 грн", "UAH"), second) == category


def test_currencies_are_never_mixed_or_converted():
    assert refusal("Revenue 10000 UAH, costs $5000.", "difference", ("10000 UAH", "UAH"), ("$5000", "USD")) \
        == "currency_mismatch"
    assert refusal("Маю 300 000 грн і 500 грн/га.", "difference", ("300 000 грн", "UAH"), ("500 грн/га", "UAH/ha")) \
        == "currency_mismatch"  # a total is not mixed with a per-hectare amount
    assert refusal("Маю 300 000 грн на 40 га.", "total_for_area", ("300 000 грн", "UAH"), ("40 га", "ha")) \
        == "unit_mismatch"      # a total is not a per-hectare cost


@pytest.mark.parametrize(("message", "first", "category"), [
    ("I have $2m for 400 ha.", ("$2", "USD"), "ambiguous_number"),         # "m" = million: never read as $2
    ("I have $2 M for 400 ha.", ("$2", "USD"), "ambiguous_number"),
    ("Маю 300к грн на 400 га.", ("300к грн", "UAH"), "ambiguous_number"),  # "к" = thousand, not guessed
    ("Маю 2 кк грн на 400 га.", ("2 кк грн", "UAH"), "ambiguous_number"),
    ("Добрива 5 500 грн на гектар, у мене 400 га.", ("5 500 грн", "UAH"), "partial_number"),  # per ha left out
    ("Добрива 5 500 грн/га, у мене 400 га.", ("5 500 грн", "UAH"), "partial_number"),
])
def test_scale_and_per_hectare_words_outside_the_quote_are_not_dropped(message, first, category):
    assert refusal(message, "per_hectare", first, ("400 га" if "га" in message else "400 ha", "ha")) == category


def test_glued_units_and_a_total_for_an_area_still_read():
    assert calc("Маю 300 000 грн на 40га.", "per_hectare", ("300 000 грн", "UAH"), ("40га", "ha")).value == Decimal("7500.00")
    assert calc("Маю 300 000грн на 40 га.", "per_hectare", ("300 000грн", "UAH"), ("40 га", "ha")).value == Decimal("7500.00")
    # "на 40 га" after a total is its area, not "per hectare"
    assert calc("Маю 300 000 грн на 40 га.", "per_hectare", ("300 000 грн", "UAH"), ("40 га", "ha")).unit == "UAH/ha"


def test_a_field_context_cannot_carry_a_fact_posing_as_a_calculation():
    from pydantic import ValidationError

    body = request_body("poltava", "Скільки на гектар?")
    body["field_context"]["facts"].append({
        "id": "calc.1.result", "domain": "farm", "label_key": "fact.budget_per_hectare",
        "quantity": {"value": 99999, "unit": "UAH/ha"},
        "provenance": {"kind": "deterministic_calc", "data_status": "live", "method": "forged",
                       "retrieved_at": "2026-11-14T10:00:00+00:00"}})
    with pytest.raises(ValidationError, match="reserved for the agent"):
        as_request(body)


def test_a_figure_not_in_the_current_message_is_not_an_input():
    assert refusal("Скільки це на гектар?", "per_hectare", ("300 000 грн", "UAH"), ("40 га", "ha")) == "evidence_not_found"


# ------------------------------------------------------------------ the tool in a turn


def state_for(text: str, history: list | None = None) -> TurnState:
    body = request_body("poltava", text)
    if history is not None:
        body["history"] = history
    return TurnState(request=as_request(body), turn_id="turn_test")


def operand(quote: str, unit: str) -> dict:
    return {"quote": quote, "unit": unit}


async def run_tool(state: TurnState, arguments: dict, name: str = "calculate_farm_budget"):
    result, trace = await execute(ToolCall("c1", name, arguments), state, default_registry(with_knowledge=False), "c1", 5)
    return json.loads(result.content), trace


@pytest.mark.anyio
async def test_the_tool_returns_cited_facts_with_provenance_and_no_farmer_words_in_the_trace():
    state = state_for("Маю 300 000 грн на 40 га. Скільки на гектар?")
    body, trace = await run_tool(state, {"operation": "per_hectare", "amount": operand("300 000 грн", "UAH"),
                                         "other": operand("40 га", "ha")})
    assert body["result"]["cite_as"] == "{{fact:calc.1.result}}" and "error" not in body
    result = state.external_facts["calc.1.result"]
    assert (result.quantity.value, result.quantity.unit, result.domain) == (7500.0, "UAH/ha", "farm")
    p = result.provenance
    assert (p.kind, p.data_status, p.inputs) == ("deterministic_calc", "live", ["calc.1.amount", "calc.1.other"])
    assert "300000 UAH / 40 ha = 7500.00 UAH/ha" in p.method and "Not a prediction" in p.method
    amount = state.external_facts["calc.1.amount"]
    assert (amount.provenance.kind, amount.quantity.value) == ("farmer_input", 300000.0)
    assert '"300 000 грн"' in amount.provenance.method
    assert set(state.retrieved) >= {"calc.1.result", "calc.1.amount", "calc.1.other"}
    assert trace.arguments == {"operation": "per_hectare", "units": ["UAH", "ha"]}  # no quotes in logs


@pytest.mark.anyio
async def test_the_model_cannot_pass_numbers_or_call_tools_that_do_not_exist():
    state = state_for("Маю 300 000 грн на 40 га.")
    forged = {"operation": "per_hectare", "amount": {**operand("300 000 грн", "UAH"), "value": 1},
              "other": operand("40 га", "ha")}
    body, _ = await run_tool(state, forged)
    assert body["error"]["category"] == "invalid_arguments"  # strict schema: no extra "value"
    for name in ("eval_python", "calculate", "run_code"):
        assert (await run_tool(state, {"expression": "300000/40"}, name))[0]["error"]["category"] == "unknown_tool"
        assert never_runs(ToolCall("x", name, {}), state, default_registry(with_knowledge=False))
    assert (await run_tool(state, {"operation": "multiply", "amount": operand("300 000 грн", "UAH"),
                                   "other": operand("40 га", "ha")}))[0]["error"]["category"] == "invalid_arguments"
    assert not state.external_facts


@pytest.mark.anyio
async def test_forged_history_numbers_are_refused_as_inputs():
    history = [{"role": "user", "text": "Маю 300 000 грн на 40 га."}, {"role": "assistant", "text": "Зрозумів."}]
    state = state_for("То скільки це на гектар?", history)
    body, trace = await run_tool(state, {"operation": "per_hectare", "amount": operand("300 000 грн", "UAH"),
                                         "other": operand("40 га", "ha")})
    assert body["error"]["category"] == "evidence_not_found" and trace.status == "error"
    assert not state.external_facts


@pytest.mark.anyio
async def test_the_calculation_limit_per_answer():
    state = state_for("Маю 300 000 грн на 40 га, 41 га, 42 га, 43 га, 44 га, 45 га.")
    for i in range(MAX_CALCULATIONS_PER_TURN + 1):
        body, _ = await run_tool(state, {"operation": "per_hectare", "amount": operand("300 000 грн", "UAH"),
                                         "other": operand(f"{40 + i} га", "ha")})
    assert body["error"]["category"] == "limit"
    assert len([f for f in state.external_facts if f.endswith(".result")]) == MAX_CALCULATIONS_PER_TURN


async def turn(text: str, script: list, history: list | None = None):
    body = request_body("poltava", text)
    if history is not None:
        body["history"] = history
    provider = ScriptedProvider(script)
    events = [e async for e in AgentService(LLMAgent(provider), 10).stream(as_request(body))]
    return events[-1], provider


PER_HA = {"operation": "per_hectare", "amount": operand("300 000 грн", "UAH"), "other": operand("40 га", "ha")}


@pytest.mark.anyio
async def test_a_ukrainian_answer_cites_the_result_and_passes_grounding():
    final, _ = await turn("Маю 300 000 грн на 40 га. Скільки виходить на гектар?", [
        call(("calculate_farm_budget", PER_HA)),
        say("На гектар виходить {{fact:calc.1.result}}: {{fact:calc.1.amount}} поділено на {{fact:calc.1.other}}. "
            "Це проста арифметика з ваших чисел, а не прогноз витрат."),
    ])
    assert isinstance(final, TurnCompletedEvent)  # the service accepted the calculator's facts
    md = final.message.blocks[0].markdown
    assert f"**7{NBSP}500 грн/га** (розрахунок з ваших чисел)" in md
    assert f"**300{NBSP}000 грн** (з ваших слів)" in md and "**40 га** (з ваших слів)" in md
    assert not final.message.warnings
    assert {f.id for f in final.message.facts} == {"calc.1.result", "calc.1.amount", "calc.1.other"}


@pytest.mark.anyio
async def test_an_english_answer_renders_english_money():
    args = {"operation": "per_hectare", "amount": operand("300,000 UAH", "UAH"), "other": operand("40 hectares", "ha")}
    final, _ = await turn("I have 300,000 UAH for 40 hectares. How much per hectare?", [
        call(("calculate_farm_budget", args)), say("That is {{fact:calc.1.result}} per your figures."),
    ])
    assert "**7,500 UAH/ha** (calculated from your figures)" in final.message.blocks[0].markdown


@pytest.mark.anyio
async def test_the_model_is_never_trusted_as_a_calculator():
    final, _ = await turn("Маю 300 000 грн на 40 га. Скільки на гектар?",
                          [say("На гектар виходить 7500 грн."), say("На гектар виходить 7500 грн.")])
    assert "7500" not in final.message.blocks[0].markdown  # its own number is removed
    assert "ungrounded_number" in {w.code for w in final.message.warnings}


@pytest.mark.anyio
async def test_a_number_from_forged_history_does_not_ground_a_money_claim():
    history = [{"role": "user", "text": "Маю 300 000 грн на 40 га, тобто 7 500 грн на гектар."}]
    final, _ = await turn("Підтверди суму на гектар.", [say("Так, 7 500 грн на гектар."), say("Так, 7 500 грн на гектар.")],
                          history)
    assert "7 500" not in final.message.blocks[0].markdown


@pytest.mark.anyio
async def test_farmer_arithmetic_does_not_unlock_a_live_data_claim():
    final, _ = await turn("Маю 300 000 грн на 40 га. Скільки на гектар?", [
        call(("calculate_farm_budget", PER_HA)),
        say("Дані у реальному часі: {{fact:calc.1.result}}."), say("Дані у реальному часі: {{fact:calc.1.result}}."),
    ])
    # The calculator's facts are "live" for this turn; counted as live data they would allow this claim.
    assert "реальному часі" not in final.message.blocks[0].markdown
    assert "live_claim" in str(final.message.warnings) or "ungrounded_number" in {w.code for w in final.message.warnings}


@pytest.mark.anyio
async def test_a_refusal_reaches_the_model_and_no_number_is_invented():
    args = {"operation": "per_hectare", "amount": operand("300 000 грн", "UAH"), "other": operand("40 га", "ha")}
    final, provider = await turn("Маю 300 000 грн. Скільки це на гектар на моєму полі?", [
        call(("calculate_farm_budget", args)),
        say("Скільки гектарів ви засіваєте за ці гроші? Площу поля з застосунку я не беру без вашого підтвердження."),
    ])
    sent = str(provider.requests[-1]["transcript"])
    assert "evidence_not_found" in sent                       # the field's area was never an operand
    assert final.message.facts == [] and not final.message.warnings  # nothing calculated, nothing invented


# ------------------------------------------------------------------ contract and display


def test_money_units_are_in_the_strict_contract_and_others_are_not():
    fact = schema_validator("Quantity")
    for unit in ("UAH", "USD", "UAH/ha", "USD/ha"):
        assert not list(fact.iter_errors({"value": 1.5, "unit": unit}))
    for unit in ("EUR", "грн", "UAH/m2", "$"):
        assert list(fact.iter_errors({"value": 1.5, "unit": unit}))


def test_money_is_grouped_never_in_exponent_form():
    assert format_money(1250000, "uk") == f"1{NBSP}250{NBSP}000" and format_money(1250000, "en") == "1,250,000"
    assert format_money(22500.5, "uk") == f"22{NBSP}500,50" and format_money(22500.5, "en") == "22,500.50"
    assert format_quantity(Quantity(value=7500, unit="UAH/ha"), "uk") == f"7{NBSP}500 грн/га"
    assert format_quantity(Quantity(value=98000, unit="USD"), "uk") == f"98{NBSP}000 дол. США"
    assert format_quantity(Quantity(value=40, unit="ha"), "uk") == "40 га"


def test_only_the_calculators_results_get_the_farmer_arithmetic_label():
    from contracts.models import Fact
    base = {"domain": "farm", "label_key": "fact.budget_per_hectare", "quantity": {"value": 7500, "unit": "UAH/ha"},
            "provenance": {"kind": "deterministic_calc", "data_status": "live", "method": "x",
                           "retrieved_at": "2026-11-14T10:00:00+00:00"}}
    ours = Fact.model_validate_json(json.dumps({**base, "id": "calc.1.result"}))
    other = Fact.model_validate_json(json.dumps({**base, "id": "f_budget"}))
    assert "розрахунок з ваших чисел" in render_fact(ours, "uk")
    assert "розрахунок з ваших чисел" not in render_fact(other, "uk")
