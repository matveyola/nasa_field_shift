"""Reconstructed T01 formatting failures: the original rejected arguments were not logged.
Fixtures enter through real server tools, never through unsigned browser weather facts.
"""

import json

import httpx2
import pytest
from agent_service.llm.provider import ToolCall, UserText
from agent_service.llm.scripted import call, say
from agent_service.tools import (
    TurnState,
    _weather_presentation,
    current_source_presentations,
    default_registry,
    execute,
    repair_evidence,
    source_presentation_text,
)
from agent_service.weather import OpenMeteoClient, facts_from_forecast, season_facts

from .conftest import as_request, make_client, request_body, schema_validator
from .test_weather import END, NOW, START, agent_with, day, forecast


def state():
    body = request_body(locale="en")
    body["field_context"]["facts"] = []
    return TurnState(as_request(body), "turn_source")


async def read(s, domains=None):
    return await execute(
        ToolCall("read", "get_field_facts", {"domains": domains or [], "fact_ids": []}),
        s,
        default_registry(with_knowledge=False),
        "read",
        5,
    )


@pytest.mark.anyio
async def test_read_key_tracks_returned_view_not_unrelated_changes():
    s = state()
    assert not (await read(s))[0].is_error
    assert (await read(s))[1].error_category == "duplicate_call"
    assert not (await read(s, ["water"]))[0].is_error
    data = forecast(5, tmin=0)
    data["daily"]["temperature_2m_min"] = [0, 0, 0, 4, 4]
    facts, _ = facts_from_forecast(data, "en", NOW)
    historical = season_facts(
        {
            START.isoformat(): day(20, 10, 1, 2),
            "2026-05-02": day(20, 10, 1, 2),
            END.isoformat(): day(20, 10, 1, 2),
        },
        START,
        END,
        None,
        "en",
        NOW,
    )
    assert len(facts) == 38 and len(historical) == 3
    s.external_facts.update({f.id: f for f in facts})
    assert (await read(s, ["water"]))[1].error_category == "duplicate_call"
    s.external_facts.update({f.id: f for f in historical})
    refreshed, trace = await read(s)
    assert not refreshed.is_error and len(trace.refs) == 41
    assert (await read(s))[1].error_category == "duplicate_call"
    assert not (await read(s, ["water"]))[0].is_error
    assert (await read(s, ["water"]))[1].error_category == "duplicate_call"


@pytest.mark.anyio
async def test_same_id_changed_content_and_authority_are_new_views():
    s = state()
    facts, _ = facts_from_forecast(forecast(1), "en", NOW)
    fact = facts[0]
    s.request.field_context.facts.append(fact)
    assert not (await read(s))[0].is_error
    s.context_attested = True
    assert not (await read(s))[0].is_error
    s.external_facts[fact.id] = fact.model_copy(deep=True)
    assert not (await read(s))[0].is_error
    s.external_facts[fact.id].quantity.value += 1
    assert not (await read(s))[0].is_error
    assert (await read(s))[1].error_category == "duplicate_call"


@pytest.mark.parametrize("lang", ["en", "uk"])
@pytest.mark.parametrize("count", [1, 5, 7])
def test_source_templates_are_bounded_and_null_is_not_zero(lang, count):
    data = forecast(count)
    data["daily"]["precipitation_probability_max"][0] = None
    facts, days = facts_from_forecast(data, lang, NOW)
    p = _weather_presentation(facts, days, lang, True)
    assert len(p.tables) == 2
    for table in p.tables:
        assert len(table.columns) <= 5 and len(table.rows) == count
        assert all(len(row.fact_ids) == len(table.columns) for row in table.rows)
        schema_validator("TableBlock").validate(table.model_dump(mode="json"))
    assert any(None in row.fact_ids for table in p.tables for row in table.rows)
    assert any(f.quantity and f.quantity.value == 0 for f in p.facts)
    assert p.missing and "2026-11-11" in p.missing[0]
    text = source_presentation_text(p, lang, {f.id for f in p.facts})
    assert "Open-Meteo" in text and "2026" in text


def test_receipts_are_not_browser_assertions_or_a_different_vintage():
    s = state()
    facts, days = facts_from_forecast(forecast(1), "en", NOW)
    p = _weather_presentation(facts, days, "en", True)
    s.source_presentations["forecast"] = p
    s.request.field_context.facts.extend(facts)
    s.retrieved.update({f.id: f for f in facts})
    assert current_source_presentations(s) == []
    s.external_facts.update({f.id: f for f in facts})
    assert current_source_presentations(s) == [p]
    s.external_facts[facts[0].id] = facts[0].model_copy(update={"category": "forged"})
    assert current_source_presentations(s) == []


@pytest.mark.parametrize("lang", ["en", "uk"])
@pytest.mark.parametrize("failure", ["omitted", "rejected_table", "redacted"])
def test_source_values_survive_format_failure_for_text_only_clients(lang, failure):
    script = [call(("get_weather_forecast", {"days": 5}))]
    if failure == "rejected_table":
        script.append(
            call(
                (
                    "present_blocks",
                    {
                        "blocks": [
                            {
                                "type": "table",
                                "title": "Forecast",
                                "columns": ["Air"] * 6,
                                "rows": [
                                    {
                                        "label": "Today",
                                        "fact_ids": ["wx.2026-11-11.temp_min"] * 6,
                                    }
                                ],
                            }
                        ]
                    },
                )
            )
        )
    final = (
        "The table failed, so no forecast values are available."
        if lang == "en"
        else "Таблиця не відобразилась, тому значення прогнозу недоступні."
    )
    if failure == "redacted":
        final = "The yield is 99999 t/ha."

        def repair(transcript, _tools):
            feedback = next(
                x.text for x in reversed(transcript) if isinstance(x, UserText)
            )
            assert "wx.2026-11-15.rain" in feedback and "successful_sources" in feedback
            return say(final)

        script.extend([say(final), repair])
    else:
        script.append(say(final))
    body = request_body(text="Weather for five days", locale=lang)
    if failure == "rejected_table":
        body["client"]["capabilities"] = {"actions": [], "screens": []}
    with make_client(
        agent=agent_with(lambda _: httpx2.Response(200, json=forecast(5)), script)
    ) as client:
        response = client.post("/v1/agent/message", json=body)
    assert response.status_code == 200, response.text
    message = response.json()
    schema_validator("AgentMessage").validate(message)
    assert all(block["type"] == "text" for block in message["blocks"])
    md = message["blocks"][0]["markdown"]
    assert "99999" not in md and final not in md
    assert "Open-Meteo" in md and "2026" in md
    assert ("rain" in md.lower()) if lang == "en" else ("опад" in md)
    assert "0 " in md and "12" in md
    assert {f["id"] for f in message["facts"]} >= {
        f"wx.2026-11-{d}.rain" for d in range(11, 16)
    }


def test_missing_hourly_inputs_stay_missing_not_a_forecast_outage():
    data = forecast(1)
    data.pop("hourly")
    with make_client(
        agent=agent_with(
            lambda _: httpx2.Response(200, json=data),
            [
                call(("get_weather_forecast", {"days": 1})),
                say("The forecast is unavailable."),
            ],
        )
    ) as client:
        message = client.post(
            "/v1/agent/message", json=request_body(locale="en")
        ).json()
    text = message["blocks"][0]["markdown"]
    assert "Hourly inputs are missing" in text and "not assessed" in text
    assert "forecast is unavailable" not in text
    assert not any(f["id"].endswith(".spray") for f in message["facts"])


@pytest.mark.anyio
async def test_duplicate_keeps_successful_receipt_and_repair_index():
    s = state()
    s.weather = OpenMeteoClient(
        "https://api.open-meteo.test/v1/forecast",
        5,
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=forecast(1))
        ),
    )
    registry = default_registry(with_knowledge=False, with_weather=True)
    tool = ToolCall("weather", "get_weather_forecast", {"days": 1})
    assert not (await execute(tool, s, registry, "weather", 5))[0].is_error
    assert (await execute(tool, s, registry, "again", 5))[
        1
    ].error_category == "duplicate_call"
    evidence = repair_evidence(s)
    assert evidence["successful_sources"] and not evidence["source_failures"]
    assert "wx.2026-11-11.rain" in json.dumps(evidence)


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_sowing_format_failure_keeps_depth_actual_period_and_regional_limit(lang):
    import datetime as dt

    today = dt.datetime.now(dt.UTC).date()
    days = [today + dt.timedelta(days=i - 3) for i in range(10)]
    raw = {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": [f"{d.isoformat()}T{h:02d}:00" for d in days for h in range(24)],
            "soil_temperature_6cm": [-2.0] * 240,
        },
    }
    script = [
        call(("check_sowing_soil", {"crop": "sunflower"})),
        say("The soil table did not display."),
    ]
    with make_client(
        agent=agent_with(lambda _: httpx2.Response(200, json=raw), script)
    ) as client:
        message = client.post(
            "/v1/agent/message", json=request_body(locale=lang)
        ).json()
    md = message["blocks"][0]["markdown"]
    assert "6 " in md and "0–10" in md and "-2 °C" in md and "8 °C" in md
    assert str(today.year) in md
    assert (
        ("not established" in md and "Forest-Steppe" in md)
        if lang == "en"
        else ("не підтверджені" in md and "Лісостеп" in md)
    )
    assert {f["id"] for f in message["facts"]} == {
        "sowing.soil_now",
        "sowing.threshold",
        "sowing.ready",
    }
    assert all(b["type"] == "text" for b in message["blocks"])


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_partial_weather_success_preserves_explicit_missing_operation(lang):
    def handler(request):
        if request.url.params.get("hourly") == "soil_temperature_6cm":
            return httpx2.Response(503)
        return httpx2.Response(200, json=forecast(1))

    script = [
        call(("get_weather_forecast", {"days": 1})),
        call(("check_sowing_soil", {"crop": "sunflower"})),
        say("The soil source could not be read."),
    ]
    with make_client(agent=agent_with(handler, script)) as client:
        message = client.post(
            "/v1/agent/message", json=request_body(locale=lang)
        ).json()
    md = message["blocks"][0]["markdown"]
    assert "12" in md
    assert (
        "no values were substituted" in md
        if lang == "en"
        else "значення не підставлялися" in md
    )
    assert not any(f["id"].startswith("sowing.") for f in message["facts"])


def test_source_templates_can_be_presented_without_date_or_column_rejection():
    from agent_service.llm.scripted import tool_results

    def present(transcript, _tools):
        p = tool_results(transcript)[-1]["presentation"]
        return call(("present_blocks", {"blocks": p["tables"]}))

    def answer(transcript, _tools):
        result = tool_results(transcript)[-1]
        assert "rejected" in result and result["rejected"] == []
        return say(
            "Use the source forecast below; follow product-label restrictions for spraying."
        )

    with make_client(
        agent=agent_with(
            lambda _: httpx2.Response(200, json=forecast(5)),
            [call(("get_weather_forecast", {"days": 5})), present, answer],
        )
    ) as client:
        message = client.post(
            "/v1/agent/message",
            json=request_body(
                locale="en",
                client={
                    "widget_version": "test",
                    "capabilities": {"actions": [], "screens": []},
                },
            ),
        ).json()
    assert sum(b["type"] == "table" for b in message["blocks"]) == 2
    assert "2026" in message["blocks"][0]["markdown"]


@pytest.mark.parametrize("date", ["2026-12-31", "2024-02-29"])
def test_projection_dates_use_source_calendar_not_selected_year(date):
    data = forecast(1)
    data["daily"]["time"] = [date]
    data["hourly"]["time"] = [f"{date}T{h:02d}:00" for h in range(24)]
    facts, days = facts_from_forecast(data, "en", NOW)
    p = _weather_presentation(facts, days, "en", True)
    assert all(f.provenance.temporal.start.isoformat() == date for f in p.facts)
    assert date[:4] in source_presentation_text(p, "en", {f.id for f in p.facts})


def test_empty_source_and_invalid_unit_do_not_gain_a_successful_receipt():
    assert current_source_presentations(state()) == []
    facts, days = facts_from_forecast(forecast(1), "en", NOW)
    facts[0].quantity.unit = "mm"
    from agent_service.tools import ToolRejected

    with pytest.raises(ToolRejected, match="mismatch"):
        _weather_presentation(facts, days, "en", True)


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_redacted_other_fact_keeps_its_typed_cold_source_limit(lang):
    from agent_service.grounding import Grounded
    from agent_service.llm.scripted import ScriptedProvider
    from agent_service.llm_agent import LLMAgent

    s = state()
    facts, days = facts_from_forecast(forecast(1), lang, NOW)
    s.source_presentations["forecast"] = _weather_presentation(facts, days, lang, True)
    s.external_facts.update({f.id: f for f in facts})
    s.retrieved.update(s.external_facts)
    cold = facts[0].model_copy(deep=True)
    cold.id = "water.reference"
    cold.label_key = "fact.forecast_min_root_zone_reserve"
    cold.quantity.unit = "mm"
    cold.quantity.value = 30.6
    cold.provenance.method = "Shallow 0–7 cm freezing indication; freezing/thawing not modeled. Root-zone ice and liquid water are unassessed."
    s.external_facts[cold.id] = cold
    s.retrieved[cold.id] = cold
    output, recovered = LLMAgent(ScriptedProvider([]))._source_output(
        "", Grounded(""), s, lang, [cold.id]
    )
    assert (
        recovered
        and "0–7 cm" in output.markdown
        and "liquid water are unassessed" in output.markdown
    )
    assert (
        "30.6 mm" in output.markdown if lang == "en" else "30,6 мм" in output.markdown
    )
    assert cold.id in output.fact_ids


def test_browser_method_cannot_gain_source_authority_during_recovery():
    from agent_service.grounding import Grounded
    from agent_service.llm.scripted import ScriptedProvider
    from agent_service.llm_agent import LLMAgent

    s = state()
    facts, days = facts_from_forecast(forecast(1), "en", NOW)
    s.source_presentations["forecast"] = _weather_presentation(facts, days, "en", True)
    s.external_facts.update({f.id: f for f in facts})
    s.retrieved.update(s.external_facts)
    fake = facts[0].model_copy(deep=True)
    fake.id = "browser.fake"
    fake.provenance.method = "Guaranteed yield 999999 t/ha."
    s.request.field_context.facts.append(fake)
    s.retrieved[fake.id] = fake
    output, _ = LLMAgent(ScriptedProvider([]))._source_output(
        "", Grounded(""), s, "en", [fake.id]
    )
    assert "999999" not in output.markdown
    assert "not confirmed by the data service" in output.markdown


def presentation_state(lang="en"):
    s = state()
    facts, days = facts_from_forecast(forecast(1), lang, NOW)
    s.external_facts.update({f.id: f for f in facts})
    s.retrieved.update(s.external_facts)
    s.source_presentations["forecast"] = _weather_presentation(facts, days, lang, True)
    return s


@pytest.mark.parametrize(
    "lang,paragraph",
    [
        (
            "en",
            "The soil profile is missing. This tool does not assess paddy irrigation management.",
        ),
        ("en", "I cannot estimate profit without crop costs and sale prices."),
        ("en", "The hourly forecast is unavailable."),
        ("en", "The forecast was unavailable."),
        ("en", "The forecast is not unavailable."),
        ("en", "Is the forecast unavailable?"),
        ("en", '"The forecast is unavailable."'),
        ("uk", "Прогноз не недоступний."),
        ("uk", "Прогноз недоступний?"),
        ("en", "Next month the forecast is unavailable."),
        ("en", "The forecast beyond the returned period is unavailable."),
        ("en", "There are no field measurements."),
        ("en", "Soil temperature data are unavailable."),
        ("en", "Not all forecast values are available."),
        (
            "en",
            "Freezing and thawing are not modeled; liquid root-zone water is unassessed.",
        ),
        (
            "en",
            "Rain probabilities are unavailable, but the daily temperature forecast is available.",
        ),
        ("uk", "Профіль ґрунту відсутній. Затоплення рисового поля не оцінено."),
        ("uk", "Без витрат і цін продажу прибуток не оцінено."),
        ("uk", "Погодинний прогноз недоступний."),
        ("uk", "Прогноз на наступний місяць недоступний."),
        ("uk", "Прогноз за межами повернутого періоду недоступний."),
        ("uk", "Польові вимірювання відсутні."),
        ("uk", "Дані температури ґрунту недоступні."),
        ("uk", "Не всі значення прогнозу доступні."),
        (
            "uk",
            "Замерзання і відтавання не моделюються; рідка вода біля коренів не оцінена.",
        ),
    ],
)
def test_omitted_values_append_without_removing_valid_caveats(lang, paragraph):
    from agent_service.llm.scripted import ScriptedProvider
    from agent_service.llm_agent import LLMAgent

    s = presentation_state(lang)
    draft = "{{fact:wx.2026-11-11.temp_max}}.\n\n" + paragraph
    agent = LLMAgent(ScriptedProvider([]))
    grounded = agent._ground(draft, s, lang)
    assert not grounded.violations
    output, recovered = agent._source_output(
        draft, grounded, s, lang, grounded.fact_ids
    )
    assert recovered and paragraph in output.markdown
    assert grounded.markdown in output.markdown
    assert "wx.2026-11-11.rain" in output.fact_ids


@pytest.mark.parametrize(
    "lang,claim",
    [
        ("en", "The forecast is unavailable."),
        ("en", "Current forecast data are missing."),
        ("en", "I cannot provide the weather forecast values."),
        (
            "en",
            "The detailed forecast table did not display, so I can't provide its weather values here.",
        ),
        ("en", "The table failed, so no forecast values are available."),
        ("uk", "Прогноз недоступний."),
        ("uk", "Дані прогнозу відсутні."),
        ("uk", "Не можу надати значення прогнозу."),
        ("uk", "Таблиця не відобразилась, тому значення прогнозу недоступні."),
    ],
)
def test_only_unqualified_matching_source_denials_are_corrected(lang, claim):
    from agent_service.llm.scripted import ScriptedProvider
    from agent_service.llm_agent import LLMAgent, _without_whole_source_denial

    s = presentation_state(lang)
    agent = LLMAgent(ScriptedProvider([]))
    grounded = agent._ground(claim, s, lang)
    assert not grounded.violations
    output, recovered = agent._source_output(claim, grounded, s, lang, [])
    assert (
        recovered and claim not in output.markdown and "Open-Meteo" in output.markdown
    )
    # A true failed request, no receipt, or a different source domain is never
    # turned into source availability by recognizing a denial's wording.
    s.source_failures["get_weather_forecast"] = "weather_unavailable"
    assert (
        _without_whole_source_denial(claim, s, current_source_presentations(s), lang)
        == claim
    )
    assert _without_whole_source_denial(claim, state(), [], lang) == claim
    s.source_failures.clear()
    s.source_presentations["history"] = s.source_presentations.pop("forecast")
    assert (
        _without_whole_source_denial(claim, s, current_source_presentations(s), lang)
        == claim
    )


@pytest.mark.anyio
async def test_only_actual_block_rejection_sets_presentation_failure():
    s = presentation_state()
    registry = default_registry(with_knowledge=False)
    result, trace = await execute(
        ToolCall(
            "ui", "propose_ui_actions", {"actions": [{"type": "not-a-real-action"}]}
        ),
        s,
        registry,
        "ui",
        2,
    )
    assert json.loads(result.content)["rejected"][0]["category"] == "unsupported_action"
    assert not s.presentation_rejected
    _, trace = await execute(
        ToolCall(
            "table",
            "present_blocks",
            {
                "blocks": [
                    {
                        "type": "table",
                        "columns": ["Air"] * 6,
                        "rows": [
                            {
                                "label": "Forecast",
                                "fact_ids": ["wx.2026-11-11.temp_max"] * 6,
                            }
                        ],
                    }
                ]
            },
        ),
        s,
        registry,
        "table",
        2,
    )
    assert "rejected:table:invalid_arguments" in trace.refs
    assert s.presentation_rejected


@pytest.mark.parametrize("apostrophe", ["’", "‘", "ʼ"])
def test_actual_t01_sentence_survives_unicode_variants_without_false_outage(apostrophe):
    # The curly-right variant is the exact recorded release978 T01 sentence;
    # the other apostrophes exercise canonicalization, not extra prose grammar.
    sentence = "The detailed forecast table did not display, so I can’t provide its weather values here.".replace(
        "’", apostrophe
    )
    script = [call(("get_weather_forecast", {"days": 5})), say(sentence)]
    with make_client(
        agent=agent_with(lambda _: httpx2.Response(200, json=forecast(5)), script)
    ) as client:
        message = client.post(
            "/v1/agent/message", json=request_body(locale="en")
        ).json()
    md = message["blocks"][0]["markdown"]
    assert sentence not in md and "Open-Meteo" in md
    assert {f["id"] for f in message["facts"]} >= {
        f"wx.2026-11-{d}.rain" for d in range(11, 16)
    }


@pytest.mark.parametrize(
    "lang,claim",
    [
        ("en", "The table failed so weather is unavailable."),
        ("uk", "Погодні дані недоступні."),
    ],
)
def test_generic_weather_requires_success_without_conflicting_weather_failure(
    lang, claim
):
    from agent_service.llm_agent import _without_whole_source_denial

    s = presentation_state(lang)
    sources = current_source_presentations(s)
    assert claim not in _without_whole_source_denial(claim, s, sources, lang)
    for tool in ("get_weather_forecast", "get_season_weather"):
        s.source_failures = {tool: "weather_unavailable"}
        assert _without_whole_source_denial(claim, s, sources, lang) == claim
    s.source_failures = {}
    s.source_presentations["sowing"] = s.source_presentations.pop("forecast")
    assert _without_whole_source_denial(claim, s, sources, lang) == claim


@pytest.mark.parametrize(
    "quoted",
    [
        "‘The forecast is unavailable.’",
        "’The forecast is unavailable.’",
        "ʼThe forecast is unavailable.ʼ",
        "The ‘forecast is unavailable’.",
    ],
)
def test_unicode_quoted_denials_remain_quoted_not_current_source_claims(quoted):
    from agent_service.llm_agent import _without_whole_source_denial

    s = presentation_state()
    assert (
        _without_whole_source_denial(quoted, s, current_source_presentations(s), "en")
        == quoted
    )
