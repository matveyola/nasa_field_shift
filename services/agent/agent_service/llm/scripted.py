"""Scripted LLM provider: plays the model's part from a script, offline and for free.

It goes through exactly the same agent loop, tool registry, grounding and validation as a
real provider. Used by the test and eval suites, and (with `demo_script`) as the
`AGENT_AGENT=scripted-llm` runtime mode so the frontend can see real tool events without a key.
"""

import asyncio
import inspect
import json
import re
from collections.abc import Callable, Sequence
from itertools import count
from typing import Any

from .. import citations, plain, scenarios, scope
from ..dev_agent import _extract_constraints
from .provider import (
    Capabilities,
    ModelResponse,
    ProviderError,
    ToolCall,
    ToolResults,
    ToolSpec,
    TranscriptItem,
    Usage,
    UserText,
)

# A step is a canned response, an exception to raise, or a function of the conversation so far.
Step = (
    ModelResponse
    | BaseException
    | Callable[[Sequence[TranscriptItem], Sequence[ToolSpec]], Any]
)


def say(text: str, stop_reason: str = "end_turn") -> ModelResponse:
    return ModelResponse(
        stop_reason=stop_reason,
        text=text,
        usage=Usage(10, len(text) // 4),
        model_id="scripted",
    )


def call(
    *calls: tuple[str, Any], text: str = "", stop_reason: str = "tool_use"
) -> ModelResponse:
    """`call(("get_field_facts", {...}), ("propose_ui_actions", {...}))` - one response, several calls."""
    ids = count(1)
    return ModelResponse(
        stop_reason=stop_reason,
        text=text,
        usage=Usage(10, 10),
        model_id="scripted",
        tool_calls=tuple(
            ToolCall(f"scripted_{next(ids)}", name, args) for name, args in calls
        ),
    )


def tool_results(transcript: Sequence[TranscriptItem]) -> list[dict]:
    """Decoded JSON of every tool result so far (helper for function steps)."""
    return [
        json.loads(r.content)
        for item in transcript
        if isinstance(item, ToolResults)
        for r in item.results
    ]


class ScriptedProvider:
    name = "scripted-llm"
    capabilities = Capabilities(
        parallel_tool_calls=True, strict_tool_schemas=True, forced_tool_choice=False
    )

    def __init__(
        self,
        script: Sequence[Step] | Callable[..., Any],
        model_id: str = "scripted",
        *,
        record_requests: bool = True,
    ):
        self.script = script
        self.model_id = model_id
        self.record_requests = record_requests
        self.requests: list[dict[str, Any]] = []  # what the agent sent, for assertions
        self._position = 0

    async def complete(
        self,
        *,
        system: str,
        transcript: Sequence[TranscriptItem],
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
    ) -> ModelResponse:
        if self.record_requests:
            self.requests.append(
                {
                    "system": system,
                    "transcript": list(transcript),
                    "tools": list(tools),
                    "max_output_tokens": max_output_tokens,
                }
            )
        if callable(self.script):
            step: Any = self.script
        else:
            if self._position >= len(self.script):
                raise ProviderError("bad_request", "script exhausted")
            step = self.script[self._position]
            self._position += 1
        if callable(step) and not isinstance(step, ModelResponse):
            step = step(transcript, tools)
            if inspect.isawaitable(step):
                step = await step
        if isinstance(step, BaseException):
            raise step
        await asyncio.sleep(
            0
        )  # a real provider call is a suspension point; keep cancellation honest
        return step

    async def aclose(self) -> None:
        return None


# Questions the demo model sends to search_knowledge instead of reading field facts.
KNOWLEDGE_QUESTION = re.compile(
    r"\bFAO\b|ФАО|стрес|stress|\bKc\b|\bKs\b|коефіцієнт|coefficient|евапотранспір|evapotranspir|"
    r"\bTAW\b|\bRAW\b|корен|root zone|depletion|виснаж|growth stage|фаз[аиі] розвитку",
    re.IGNORECASE,
)
_FARMER = re.compile(r"<farmer_message>\n(.*?)\n</farmer_message>", re.DOTALL)
_APPLICATION = re.compile(
    r"<application_context>\n(.*?)\n</application_context>", re.DOTALL
)


def _context_data(pattern: re.Pattern[str], first_user: str) -> dict[str, Any]:
    match = pattern.search(first_user)
    try:
        data = json.loads(match.group(1)) if match else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _quotable_sentence(
    text: str, terms: set[str] | frozenset[str] = frozenset()
) -> str | None:
    """One whole sentence of the passage, verbatim, short enough to show (the server re-checks it). With
    question terms: the sentence sharing most of them (first on ties), and at least two (or all, for a shorter
    question), so a passage that does not talk about the question, or shares one generic word with it (e.g. a
    bibliography line with "water"), is not quoted at all."""
    best, best_overlap = None, min(2, len(terms)) - 1 if terms else -1
    for sentence in re.split(r"(?<=\.)\s+", text.replace("\n", " ")):
        sentence = sentence.strip()
        if (
            60 <= len(sentence) <= 240
            and sentence.endswith(".")
            and '"' not in sentence
            and "}" not in sentence
        ):
            overlap = len(terms & citations.stems(sentence)) if terms else 0
            if overlap > best_overlap:
                best, best_overlap = sentence, overlap
    return best


def _knowledge_answer(results: dict, uk: bool, question: str = "") -> ModelResponse:
    terms = citations.stems(question)
    lines = []
    for r in results.get("results", []):
        if r.get("kind") != "prose" or len(lines) == 2:
            continue
        quote = _quotable_sentence(r["text"], terms)
        if quote:
            # the whole section path: a heading may itself contain " > " ("With water stress (Dr > RAW)")
            section = r["locator"].split(";")[0] or r["source"].split(", ")[-1]
            lines.append(
                f'- «{section}»: “{quote[:-1]}” {{{{cite:{r["ref"]}|"{quote}"}}}}.'
            )
    if not lines:
        return say(
            "Демо-режим: у посібнику ФАО про воду для рослин (FAO-56) не знайшлося відповідного уривку."
            if uk
            else "Demo mode: the FAO guide on crop water use (FAO-56) has no matching passage."
        )
    header = (
        "Демо-режим (не штучний інтелект): я нічого не вигадую, лише показую знайдені уривки з посібника ФАО "
        "про воду для рослин (FAO-56). Цитати — мовою оригіналу, англійською."
        if uk
        else "Demo mode (not AI): I make nothing up, I only show passages found in the FAO guide on crop water use "
        "(FAO-56)."
    )
    return say(header + "\n\n" + "\n".join(lines))


ROTATION_QUESTION = re.compile(
    r"сівозмін|\brotations?\b|порівня|\bcompare\b", re.IGNORECASE
)
_METRICS = {
    "uk": {
        "water_risk": "ризик нестачі води",
        "soil_cover_days": "ґрунт укритий рослинами",
        "n_input": "азот у розрахунку",
        "yield_risk": "ризик для врожаю",
        "yield_loss_water": "втрата врожаю через нестачу води",
    },
    "en": {
        "water_risk": "risk of water shortage",
        "soil_cover_days": "soil covered by plants",
        "n_input": "nitrogen in the calculation",
        "yield_risk": "risk to the harvest",
        "yield_loss_water": "harvest lost to water shortage",
    },
}


def _comparison_table(result: dict, uk: bool) -> dict:
    """The comparison as a present_blocks table: rotations as rows, the service's measures as columns (fact refs)."""
    lang = "uk" if uk else "en"
    rotations = result["rotations"][:10]
    metrics = list(dict.fromkeys(m for r in rotations for m in r["scores"]))[:5]
    return {
        "type": "table",
        "title": "Порівняння сівозмін" if uk else "Rotation comparison",
        "columns": [_capital(_METRICS[lang].get(m, m)) for m in metrics],
        "rows": [
            {
                "label": plain.rotation(r["rotation_id"], lang),
                "fact_ids": [
                    r["scores"][m]["ref"] if m in r["scores"] else None for m in metrics
                ],
            }
            for r in rotations
        ],
    }


def _comparison_answer(result: dict, uk: bool, header: bool = True) -> ModelResponse:
    lang = "uk" if uk else "en"
    synthetic = result.get("service_mode") != "live" or "fixture" in result.get(
        "data_status", []
    )
    name = scenarios.label(result["scenario"], lang)
    if uk:
        lines = [
            ("Демо-режим (не штучний інтелект). " if header else "")
            + f"Окрема програма розрахувала сівозміни для варіанту «{name}». Це розрахунок, а не справжній урожай"
            + (", і числа поки **вигадані для перевірки**." if synthetic else ".")
        ]
    else:
        lines = [
            ("Demo mode (not AI). " if header else "")
            + f"A separate program calculated the rotations for the “{name}” case. This is a calculation, not a "
            "real harvest"
            + (
                ", and the numbers are **made up for testing** for now."
                if synthetic
                else "."
            )
        ]
    if result.get("constraints_sent"):
        lines.append(
            "Ваших підтверджених умов цей розрахунок поки не враховує."
            if uk
            else "This calculation does not take into account your confirmed conditions yet."
        )
    for r in result["rotations"]:
        crops = " → ".join(plain.crop(step["crop"], lang) for step in r["sequence"])
        scores = "; ".join(
            f"{_METRICS[lang].get(m, m)} {v['cite_as']}" for m, v in r["scores"].items()
        )
        place = f"місце {r['rank']}" if uk else f"place {r['rank']}"
        line = f"- **{plain.rotation(r['rotation_id'], lang)}** ({place}): {crops}. {_capital(scores)}."
        lines.append(
            line
            + "".join(
                " " + _conflict_text(c, lang)
                for c in r.get("farmer_constraint_conflicts", [])
            )
        )
    return say("\n".join(lines))


def _conflict_text(conflict: dict, lang: str) -> str:
    """One confirmed farmer rule a compared rotation breaks, in plain words (tools.constraint_conflicts)."""
    if conflict["type"] == "excluded_crops":
        crops = ", ".join(plain.crop(c, lang) for c in conflict["crops"])
        return (
            f"Тут є культура, яку ви виключили: {crops}."
            if lang == "uk"
            else f"It includes a crop you excluded: {crops}."
        )
    years, confirmed = conflict["years"], conflict["confirmed_years"]
    return (
        f"Її тривалість ({years} р.) не збігається з вашою підтвердженою умовою ({confirmed} р.)."
        if lang == "uk"
        else f"Its length ({years} years) differs from your confirmed condition ({confirmed} years)."
    )


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


MOISTURE = re.compile(r"вологіст|soil\s+moisture", re.IGNORECASE)
WATER_QUESTION = re.compile(
    r"прогноз.{0,16}вод|водн.{0,4}\s+баланс|запас.{0,10}вод|водн.{0,4}\s+стрес|нестач.{0,5}\s+вод|"
    r"water forecast|water balance|water reserve|water shortage|water stress|root.zone reserve",
    re.IGNORECASE,
)
VEGETATION_QUESTION = re.compile(
    r"\bndvi\b|рослинност|неоднорід|стан\s+посів|vegetation|crop condition",
    re.IGNORECASE,
)
FAO_NAMED = re.compile(r"\bFAO\b|ФАО", re.IGNORECASE)
SMAP_LAYER = "l_smap_rootzone"
NDVI_LAYER = "l_ndvi_variability"
_CONDITION = {"uk": "умову за вашими словами", "en": "the condition in your words"}


def _scenario_for(farmer: str) -> str:
    """The scenario the farmer named (first in scenarios.WORDS order), else typical. Never another one."""
    named = scenarios.mentioned(farmer)
    return next((sid for sid in scenarios.WORDS if sid in named), "typical")


def _not_computed(error: dict, scenario: str, uk: bool) -> str:
    if error.get("category") in ("unsupported_scenario", "scenario_not_requested"):
        name = scenarios.label(scenario, "uk" if uk else "en")
        # One line: the server's own question below this answer explains why and offers the scenarios it has.
        return (
            f"Для варіанту «{name}» порівняння сівозмін не розраховано."
            if uk
            else f"No rotation comparison was calculated for the “{name}” case."
        )
    return (
        "Порівняння сівозмін зараз не розраховано: програма розрахунку не дала результату."
        if uk
        else "No rotation comparison was calculated: the calculation program gave no result."
    )


def _constraint_proposals(farmer: str) -> list[dict]:
    """The farmer's explicit statements, found with the rules agent's own patterns (exact words as evidence).
    The propose_constraints tool re-checks the evidence against the message and the field's constraints."""
    return [
        {
            "type": c.type,
            "value": c.value,
            "basis": "explicit",
            "evidence": c.evidence,
            "confidence": 1,
        }
        for c in _extract_constraints(farmer)
    ]


def _field_views(
    farmer: str, context: dict[str, Any], uk: bool
) -> tuple[list[str], list[dict[str, Any]]]:
    domains, actions = [], []
    moisture = bool(MOISTURE.search(farmer))
    water = moisture or bool(WATER_QUESTION.search(farmer))
    vegetation = bool(VEGETATION_QUESTION.search(farmer))
    layers = {layer["layer_id"] for layer in context.get("map_layers", [])}
    charts = {
        chart["chart_id"]: chart["metric_ids"] for chart in context.get("charts", [])
    }

    if water:
        domains.append("water")
        if moisture and SMAP_LAYER in layers:
            actions.append(
                {
                    "type": "map.set_layers",
                    "layer_ids": [SMAP_LAYER],
                    "mode": "replace",
                    "trigger": "auto",
                    "label": "Вологість ґрунту (супутник)"
                    if uk
                    else "Soil moisture (satellite)",
                    "reason": "",
                }
            )
        for chart, label in (
            (
                "c_water_forecast",
                "Відкрити прогноз води" if uk else "Open water forecast",
            ),
            (
                "c_water_season",
                "Відкрити водний баланс" if uk else "Open water balance",
            ),
        ):
            if "m_rootzone_reserve" in charts.get(chart, []):
                actions.append(
                    {
                        "type": "chart.focus",
                        "chart_id": chart,
                        "metric_ids": ["m_rootzone_reserve"],
                        "highlight": None,
                        "trigger": "chip",
                        "label": label,
                        "reason": "",
                    }
                )
    if vegetation:
        domains.append("vegetation")
        if "m_ndvi" in charts.get("c_vegetation", []):
            actions.append(
                {
                    "type": "chart.focus",
                    "chart_id": "c_vegetation",
                    "metric_ids": ["m_ndvi"],
                    "highlight": None,
                    "trigger": "chip",
                    "label": "Відкрити сезонний NDVI" if uk else "Open seasonal NDVI",
                    "reason": "",
                }
            )
        if NDVI_LAYER in layers:
            actions.append(
                {
                    "type": "map.set_layers",
                    "layer_ids": [NDVI_LAYER],
                    "mode": "add",
                    "trigger": "chip",
                    "label": "Показати неоднорідність посівів"
                    if uk
                    else "Show vegetation variability",
                    "reason": "",
                }
            )
    return domains, actions


def _request_calls(
    farmer: str, offered: set[str], uk: bool, context: dict[str, Any]
) -> list[tuple[str, Any]]:
    """Read requested facts and propose available views, constraints or a rotation comparison.
    Every call goes through the real tool registry and its validation."""
    calls: list[tuple[str, Any]] = []
    if (
        proposals := _constraint_proposals(farmer)
    ) and "propose_constraints" in offered:
        calls.append(("propose_constraints", {"constraints": proposals}))
    domains, actions = _field_views(farmer, context, uk)
    if domains:
        calls.append(("get_field_facts", {"domains": domains, "fact_ids": []}))
    if actions and "propose_ui_actions" in offered:
        calls.append(("propose_ui_actions", {"actions": actions}))
    if ROTATION_QUESTION.search(farmer) and "compare_rotations" in offered:
        calls.append(
            (
                "compare_rotations",
                {"scenario": _scenario_for(farmer), "rotation_ids": []},
            )
        )
    return calls


def _request_answer(
    farmer: str, calls: list[tuple[str, Any]], results: list[dict], uk: bool
) -> ModelResponse:
    by_tool = {name: result for (name, _), result in zip(calls, results)}
    lang = "uk" if uk else "en"
    lines = [
        "Демо-режим (не штучний інтелект): показую лише те, що знайшлося в даних."
        if uk
        else "Demo mode (not AI): I only show what was found in the data."
    ]
    constraint = by_tool.get("propose_constraints")
    if constraint is not None:
        evidence = {c["type"]: c["evidence"] for c in _constraint_proposals(farmer)}
        for a in constraint.get("accepted", []):
            lines.append(
                f"Пропоную {_CONDITION[lang]} «{evidence.get(a['type'], '')}». Вона не збережеться, доки ви не "
                "підтвердите її в застосунку."
                if uk
                else f"I propose {_CONDITION[lang]} “{evidence.get(a['type'], '')}”. "
                "It is not saved until you confirm it in the app."
            )
        for u in constraint.get("already_in_context", []):
            lines.append(
                f"«{evidence.get(u['type'], '')}» — це вже підтверджено для поля, нову умову не пропоную."
                if uk
                else f"“{evidence.get(u['type'], '')}” is already confirmed for this field; I propose nothing new."
            )
        if constraint.get("rejected"):
            lines.append(
                "Частину умов не зафіксовано: уточніть, будь ласка."
                if uk
                else "Some conditions were not recorded: please clarify."
            )
    if "get_field_facts" in by_tool:
        facts = [
            fact
            for fact in by_tool["get_field_facts"].get("facts", [])
            if fact["availability"] == "available"
        ]
        if facts:
            lines.append(
                "Збережені дані вибраного поля."
                if uk
                else "Saved evidence for the selected field."
            )
            lines += [
                f"- {_capital(plain.name(f['label'], lang))}: {f['cite_as']}"
                for f in facts[:8]
            ]
        else:
            lines.append(
                "Для цього запиту ще немає збережених даних поля."
                if uk
                else "This field has no saved evidence for that request yet."
            )
    if MOISTURE.search(farmer):
        proposed = dict(calls).get("propose_ui_actions", {}).get("actions", [])
        has_smap = any(action.get("layer_ids") == [SMAP_LAYER] for action in proposed)
        if has_smap:
            lines.append(
                "Пропоную показати на карті вологість ґрунту (супутник NASA SMAP)."
                if uk
                else "I suggest showing soil moisture on the map (NASA SMAP satellite)."
            )
        else:
            lines.append(
                "Карти вологості для цього поля немає, тому карту не змінюю."
                if uk
                else "There is no soil-moisture map for this field, so the map is unchanged."
            )
    comparison = next(
        (r for r in results if r.get("status") == "completed" and "rotations" in r),
        None,
    )
    text = "\n".join(lines)
    if comparison is not None:
        if (
            len(lines) == 1
        ):  # nothing but the header: the comparison answer carries its own
            return _comparison_answer(comparison, uk)
        return say(
            text + "\n\n" + _comparison_answer(comparison, uk, header=False).text
        )
    if "compare_rotations" in by_tool:
        text += "\n\n" + _not_computed(
            by_tool["compare_rotations"].get("error", {}), _scenario_for(farmer), uk
        )
    elif ROTATION_QUESTION.search(
        farmer
    ):  # the tool is not offered: no simulation service is connected
        text += "\n\n" + (
            "Програму для порівняння сівозмін ще не підключено, тому порівняння не розраховано."
            if uk
            else "The rotation comparison program is not connected yet, so no comparison was calculated."
        )
    return say(text)


def demo_script(
    transcript: Sequence[TranscriptItem], tools: Sequence[ToolSpec]
) -> ModelResponse:
    """Stateless demo model for `AGENT_AGENT=scripted-llm`: one backend for the whole demo.

    - A question that names FAO goes to `search_knowledge`; the answer quotes the passage sentences that share
      the question's terms, with verified cite tokens.
    - A message with requests (a stated condition, soil moisture, a rotation comparison) gets one round with one
      call per request (propose_constraints; get_field_facts + map layer; compare_rotations for the scenario the
      farmer named, never another), then a comparison-panel chip if a comparison completed, then the answer.
    - Other knowledge questions go to `search_knowledge`; everything else reads the field's facts.
    It never writes a number itself: every value is a server-rendered fact token, every citation is checked."""
    results = tool_results(transcript)
    first_user = next((i.text for i in transcript if isinstance(i, UserText)), "")
    farmer = str(_context_data(_FARMER, first_user).get("text") or "")
    context = _context_data(_APPLICATION, first_user)
    uk = context.get("response_language") == "uk"
    offered = {t.name for t in tools}
    comparison = next(
        (r for r in results if r.get("status") == "completed" and "rotations" in r),
        None,
    )
    if (
        comparison is not None and "accepted" not in results[-1]
    ):  # the comparison is the last call of its round
        label = "Відкрити порівняння сівозмін" if uk else "Open the rotation comparison"
        blocks = (
            [("present_blocks", {"blocks": [_comparison_table(comparison, uk)]})]
            if "present_blocks" in offered
            else []
        )
        return call(
            (
                "propose_ui_actions",
                {
                    "actions": [
                        {
                            "type": "panel.open",
                            "panel": "comparison",
                            "ref_id": comparison["result_id"],
                            "trigger": "chip",
                            "label": label,
                            "reason": "",
                        }
                    ]
                },
            ),
            *blocks,
        )
    knowledge = "search_knowledge" in offered and (
        FAO_NAMED.search(farmer) or KNOWLEDGE_QUESTION.search(farmer)
    )
    calls = (
        []
        if knowledge and FAO_NAMED.search(farmer)
        else _request_calls(farmer, offered, uk, context)
    )
    if calls or (ROTATION_QUESTION.search(farmer) and not knowledge):
        if not results and calls:
            return call(
                *calls, text="Scripted demo model (not an LLM): one call per request."
            )
        return _request_answer(farmer, calls, results[: len(calls)], uk)
    if not results:
        if knowledge:
            return call(
                (
                    "search_knowledge",
                    {
                        "query": farmer[:300],
                        "topic": "any",
                        "content": "text",
                        "max_results": 5,
                    },
                ),
                text="Scripted demo model (not an LLM): searching FAO-56.",
            )
        if farmer and not scope.asks_about_the_field(
            farmer
        ):  # no rule matched and it is not about the field
            return say(scope.DEMO_LIMITS["uk" if uk else "en"])
        return call(
            ("get_field_facts", {"domains": [], "fact_ids": []}),
            text="Scripted demo model (not an LLM): reading the field context.",
        )
    last = results[-1]
    if "results" in last:
        return _knowledge_answer(last, uk, farmer)
    facts = last.get("facts", [])
    if not facts:
        return say(
            "Демо-режим: про це поле ще немає даних."
            if uk
            else "Demo mode: there is no data about this field yet."
        )
    header = (
        "Демо-режим (не штучний інтелект). Ось що відомо про ваше поле:"
        if uk
        else "Demo mode (not AI). Here is what is known about your field:"
    )
    lang = "uk" if uk else "en"
    lines = [
        f"- {_capital(plain.name(f['label'], lang))}: {{{{fact:{f['ref']}}}}}"
        for f in facts[:8]
    ]
    return say(header + "\n" + "\n".join(lines))
