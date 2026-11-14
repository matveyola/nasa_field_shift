from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Literal, Protocol
from uuid import UUID

from field_shift_science.fields.schemas import FieldRead
from field_shift_science.simulation.schemas import WaterForecast, WaterResult

from field_shift.modules.agent.schemas import (
    AgentAction,
    AgentCitation,
    AgentProviderName,
    MessageRead,
)
from field_shift.modules.farm.schemas import ScoutingEvidence
from field_shift.modules.imagery.vegetation_schemas import VegetationSeason


@dataclass(frozen=True, slots=True)
class AgentContext:
    water: WaterResult | None
    run_id: UUID | None
    history: tuple[MessageRead, ...]
    field_id: str = ""
    season: int = 2026
    forecast: WaterForecast | None = None
    vegetation: VegetationSeason | None = None
    today: date | None = None
    field: FieldRead | None = None
    captured_at: datetime | None = None
    scenario: str = "base"
    locale: Literal["uk", "en"] | None = None
    conversation_id: UUID | None = None
    scouting: tuple[ScoutingEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    message: str
    demo: bool
    actions: tuple[AgentAction, ...]
    provider: AgentProviderName = "template"
    citations: tuple[AgentCitation, ...] = ()
    options: tuple[str, ...] = ()


class AgentProvider(Protocol):
    def reply(self, message: str, context: AgentContext) -> AgentAnswer: ...


class DemoAgentProvider:
    """Deterministic assistant, grounded only in supplied persisted field evidence."""

    def reply(self, message: str, context: AgentContext) -> AgentAnswer:
        lowered = message.casefold()
        ukrainian = any("а" <= char <= "я" or char in "іїєґ" for char in lowered)
        if any(token in lowered for token in ("rotation", "plan b", "сівозмін", "план б")):
            return AgentAnswer(
                "Порівняйте плани за однаковим погодним сценарієм, "
                "щоб оцінити зміну водного стресу."
                if ukrainian
                else "Compare both plans under the same weather scenario "
                "to see the change in stress.",
                True,
                (AgentAction(label="Compare plans", target="rotations"),),
            )
        if context.water is not None and context.run_id is not None:
            return _scenario_answer(context, ukrainian)
        if any(
            token in lowered
            for token in (
                "ndvi",
                "vegetat",
                "heterogen",
                "посів",
                "рослин",
                "неоднорід",
                "супут",
                "індекс",
            )
        ):
            return _vegetation_answer(context, ukrainian)
        answer = _forecast_answer(context, ukrainian)
        if any(
            token in lowered
            for token in (
                "attention",
                "inspect",
                "signal",
                "пріоритет",
                "уваг",
                "перевір",
                "огля",
                "сигнал",
            )
        ):
            vegetation = _vegetation_answer(context, ukrainian)
            return AgentAnswer(
                answer.message + "\n\n" + vegetation.message,
                True,
                answer.actions + vegetation.actions,
            )
        return answer


def _number(value: float, ukrainian: bool, digits: int = 1) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if ukrainian else text


def _scenario_answer(context: AgentContext, ukrainian: bool) -> AgentAnswer:
    result = context.water
    assert result is not None
    stress = result.first_stress_date
    if ukrainian:
        prefix = "Демонстраційний сценарій" if result.demo else "Збережений історичний сценарій"
        summary = (
            f"За цим сценарієм водний стрес починається {stress.isoformat()}."
            if stress
            else "За цим сценарієм запас не опускається нижче порога стресу."
        )
        message = (
            f"{prefix}. {summary} Мінімальний запас у зоні коренів — "
            f"{_number(result.min_reserve_mm, True)} мм. "
            "Це розрахунок сценарію, не поточний прогноз."
        )
    else:
        prefix = "Demo scenario" if result.demo else "Saved historical scenario"
        summary = (
            f"In this scenario, stress begins on {stress:%b} {stress.day}."
            if stress
            else "In this scenario, the reserve stays above or at the stress threshold."
        )
        message = (
            f"{prefix}. {summary} The minimum root-zone reserve is {result.min_reserve_mm:g} mm. "
            "This is a scenario calculation, not the current forecast."
        )
    return AgentAnswer(
        message,
        True,
        (
            AgentAction(label="View daily balance", target="water", run_id=context.run_id),
            AgentAction(label="Compare plans", target="rotations"),
        ),
    )


def _forecast_answer(context: AgentContext, ukrainian: bool) -> AgentAnswer:
    forecast = context.forecast
    today = context.today or datetime.now(UTC).date()
    days = (
        sorted((day for day in forecast.days if day.date >= today), key=lambda day: day.date)
        if forecast
        else []
    )
    valid = forecast is not None and (
        forecast.field_id == context.field_id
        and context.season == today.year == forecast.season
        and forecast.initialized_at.tzinfo is not None
        and today - timedelta(days=3) <= forecast.initialized_at.astimezone(UTC).date() <= today
        and isfinite(forecast.threshold_mm)
        and forecast.threshold_mm >= 0
        and bool(days)
        and days[0].date == today
        and all(isfinite(day.reserve_mm) and day.reserve_mm >= 0 for day in days)
        and all(b.date - a.date == timedelta(days=1) for a, b in zip(days, days[1:]))
    )
    if not valid or forecast is None:
        message = (
            "Для вибраного поля та сезону немає актуального перевіреного прогнозу води. "
            "Оновіть прогноз у вкладці «Вода». За відсутності даних ризик нестачі води невідомий."
            if ukrainian
            else "No current verified water forecast is saved for the selected field and season. "
            "Update it in Water. Missing data means the water-shortage risk is unknown."
        )
        return AgentAnswer(
            message, True, (AgentAction(label="View water forecast", target="water"),)
        )
    minimum = min(day.reserve_mm for day in days)
    stress = next((day.date for day in days if day.reserve_mm < forecast.threshold_mm), None)
    start, end = days[0].date.isoformat(), days[-1].date.isoformat()
    initialized = forecast.initialized_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if ukrainian:
        summary = (
            f"Запас нижче порога водного стресу очікується з {stress.isoformat()}."
            if stress
            else "У межах цього прогнозу нестачі води не очікується."
        )
        message = (
            f"{summary} Період {start}–{end}: мінімальний запас у зоні коренів "
            f"{_number(minimum, True)} мм, поріг {_number(forecast.threshold_mm, True)} мм. "
            f"Це модельна оцінка FAO-56 за погодою {forecast.provider}, "
            f"сітка {forecast.grid_resolution_km} км; запуск {initialized}. "
            "Розрахунок використовує довідковий ґрунт і не враховує зрошення."
        )
    else:
        summary = (
            f"The reserve is forecast to fall below the water-stress threshold from {stress}."
            if stress
            else "No water shortage is expected within this forecast."
        )
        message = (
            f"{summary} Period {start}–{end}: minimum root-zone reserve "
            f"{minimum:g} mm, threshold {forecast.threshold_mm:g} mm. "
            f"This is a FAO-56 model estimate using {forecast.provider} weather, "
            f"{forecast.grid_resolution_km} km grid; initialized {initialized}. "
            "The calculation uses reference soil and assumes no irrigation."
        )
    return AgentAnswer(
        message,
        True,
        (
            AgentAction(
                label="View water forecast", target="water", forecast_day=stress or days[0].date
            ),
        ),
    )


def _vegetation_answer(context: AgentContext, ukrainian: bool) -> AgentAnswer:
    vegetation = context.vegetation
    today = context.today or datetime.now(UTC).date()
    action = AgentAction(label="View vegetation analysis", target="fields", analysis="vegetation")
    observations = (
        [
            sample
            for sample in vegetation.observations
            if (
                sample.date.year == context.season
                and sample.date <= today
                and all(
                    isfinite(value) and -1 <= value <= 1
                    for value in (sample.mean_ndvi, sample.p10_ndvi, sample.p90_ndvi)
                )
                and sample.p10_ndvi <= sample.p90_ndvi
                and isfinite(sample.valid_percent)
                and 70 <= sample.valid_percent <= 100
                and sample.pixel_count >= 10
            )
        ]
        if vegetation and vegetation.field_id == context.field_id
        else []
    )
    if not observations or vegetation is None or vegetation.season != context.season:
        message = (
            "Для вибраного поля та сезону немає збереженого перевіреного спостереження Sentinel-2. "
            "Запустіть сезонний аналіз на карті. "
            "Хмари або прогалини в даних не означають низький NDVI."
            if ukrainian
            else "No verified Sentinel-2 observation is saved for this field and season. "
            "Run the seasonal analysis on the map. Clouds or missing data do not mean low NDVI."
        )
        return AgentAnswer(message, True, (action,))
    latest = max(observations, key=lambda sample: sample.date)
    acquired = latest.date.isoformat()
    message = (
        f"Sentinel-2, {acquired}: середній NDVI {_number(latest.mean_ndvi, True, 2)}; "
        f"валідні пікселі покривають {_number(latest.valid_percent, True)}% аналізованої площі. "
        "Це супутниковий індекс рослинності, не вимір вологості ґрунту."
        if ukrainian
        else f"Sentinel-2 on {acquired}: mean NDVI {latest.mean_ndvi:.2f}; "
        f"valid pixels cover {latest.valid_percent:g}% of the analysed footprint. "
        "This is a satellite vegetation index, not a soil-moisture measurement."
    )
    if (today - latest.date).days > 30:
        message += (
            " Спостереження старше 30 днів: це архівні дані, "
            "які не підтверджують поточний стан поля."
            if ukrainian
            else " This observation is over 30 days old: "
            "it is archival evidence, not the current field state."
        )
        return AgentAnswer(message, True, (action,))
    layer = vegetation.heterogeneity
    valid_layer = (
        layer is not None
        and layer.date == latest.date
        and all(
            (
                isfinite(layer.low_area_percent),
                0 <= layer.low_area_percent <= 100,
                isfinite(layer.median_ndvi),
                -1 <= layer.median_ndvi <= 1,
                isfinite(layer.low_threshold),
                abs(layer.low_threshold - (layer.median_ndvi - 0.15)) <= 0.001,
            )
        )
    )
    if valid_layer and layer is not None:
        message += (
            f" {_number(layer.low_area_percent, True)}% спостереженої площі має NDVI "
            "щонайменше на 0,15 нижче медіани поля."
            if ukrainian
            else f" {layer.low_area_percent:g}% of observed area has NDVI "
            "at least 0.15 below the field median."
        )
        if layer.low_area_percent >= 10:
            message += (
                " Це привід оглянути виділені зони: "
                "правило спрацьовує від 10% спостереженої площі. "
                "Це прозоре правило перевірки, а не діагноз хвороби чи оцінка втрати врожаю."
                if ukrainian
                else " Inspect the highlighted zones: this rule triggers at 10% of observed area. "
                "It is an inspection heuristic, not a disease diagnosis or a yield-loss estimate."
            )
        else:
            message += (
                " Правило огляду від 10% не спрацювало; це не підтверджує відсутність проблем."
                if ukrainian
                else " The 10% inspection rule is not triggered; "
                "this does not confirm the absence of problems."
            )
    return AgentAnswer(message, True, (action,))
