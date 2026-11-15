"""Deterministic development agent (M1).

Rule-based keyword matching. It is NOT a language model and NOT agronomic advice. It exists
so the API, the stream protocol and the frontend can be built and tested without an LLM key.

What it may do:
- relay facts that are already in the request's FieldContext, verbatim, with their provenance;
- extract a small set of farmer constraints, quoting the exact text span as evidence;
- propose UI actions that reference layers and charts present in the FieldContext.

What it never does: invent measurements, run or pretend to run a simulation, or read files.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from contracts.models import (
    AgentMessage,
    AgentRequest,
    AgentWarning,
    Constraint,
    Fact,
    FertilizerBudgetConstraint,
    FieldContext,
    IrrigationConstraint,
    Provenance,
    ResponseMeta,
    SetConstraintsAction,
    SetLayersAction,
    SetScenarioAction,
    TextBlock,
    TextDeltaEvent,
    ToolFinishedEvent,
    ToolName,
    ToolStartedEvent,
    UIActionEnvelope,
)

from . import constraint_evidence, plain, scope
from .agent import AgentOutput
from .attestation import UNTRUSTED, RequestTrust
from .grounding import format_quantity as _quantity
from .grounding import paragraph_chunks as _chunks
from .prompts import response_language as _language

NAME = "deterministic-dev"
RULES_VERSION = "dev-rules-0.1"

_I = re.IGNORECASE

# --------------------------------------------------------------- intent rules

NO_IRRIGATION = re.compile(
    r"(?:немає|нема|відсутн\w*)\s+поливу|поливу\s+(?:немає|нема)|без\s+поливу|не\s+(?:маю|маємо|має|мають)\s+полив\w*"
    r"|no\s+irrigation|without\s+irrigation|not\s+irrigated|(?:don't|do\s+not)\s+(?:have\s+)?irrigat\w*",
    _I,
)
HAS_IRRIGATION = re.compile(
    r"(?<!\w)(?:є|маю)\s+полив\w*|(?:have|has)\s+irrigation|irrigated\s+field", _I
)
REDUCE_NITROGEN = re.compile(
    r"(?:зменшити|скоротити|знизити|мінімум|менше)\s+(?:\w+\s+){0,3}азот\w*"
    r"|(?:reduce|cut|lower|minimi[sz]e|less)\s+(?:\w+\s+){0,3}nitrogen",
    _I,
)
REDUCE_FERTILIZER = re.compile(
    r"(?:зменшити|скоротити|знизити|мінімум|менше)\s+(?:\w+\s+){0,3}добрив\w*"
    r"|(?:reduce|cut|lower|minimi[sz]e|less)\s+(?:\w+\s+){0,3}fertili[sz]\w*",
    _I,
)
MAP_INTENT = re.compile(r"карт[іауи]|шар|\bmap\b|\blayers?\b", _I)
SHOW_INTENT = re.compile(r"покаж\w*|\bshow\b", _I)
SIMULATION_INTENT = re.compile(
    r"сівозмін|\brotations?\b|що\s+(?:сіяти|посіяти)|what\s+(?:should\s+i\s+|to\s+)?(?:plant|sow|grow)"
    r"|урожайн|\byields?\b|simulat|симуляц|змоделю",
    _I,
)
SCENARIOS = (
    ("drought_2024", re.compile(r"посух|drought", _I)),
    ("heat_plus_2_5c", re.compile(r"спек|heat", _I)),
)


@dataclass(frozen=True)
class Topic:
    key: str
    patterns: tuple[str, ...]  # matched against the farmer message
    data_keys: tuple[
        str, ...
    ]  # matched against fact label_key and layer id / label_key


TOPICS = (
    Topic(
        "soil_moisture", (r"волог", r"moisture"), ("soil_moisture", "smap", "rootzone")
    ),
    Topic("precipitation", (r"опад", r"дощ", r"precip", r"\brain"), ("precip",)),
    Topic("soil_ph", (r"\bph\b", r"кислотн", r"acidity"), ("soil_ph",)),
    Topic("vegetation", (r"ndvi", r"рослинн", r"vegetation"), ("ndvi",)),
    Topic("temperature", (r"температур", r"temperature"), ("temperature", "t2m")),
    Topic("slope", (r"схил", r"рельєф", r"slope", r"terrain"), ("slope",)),
)

# --------------------------------------------------------------- texts

# Read by farmers: plain words, no product codes or jargon (see plain.py). Honesty stays: the banner says this is
# not AI, demo values say so, estimates are never called measurements, and nothing simulated is claimed.
TEXT = {
    "uk": {
        "banner": "_Демо-режим: відповідь складено за готовими правилами, а не штучним інтелектом._",
        "attachments": "Фото й голосові повідомлення поки не обробляються.",
        "constraints": "З ваших слів я зрозумів такі умови. Підтвердіть їх або приберіть:",
        "constraints_known": "Ці умови вже збережені для поля, тож я їх не повторюю.",
        "apply_constraints": "Врахувати ці умови в порівнянні",
        "map_on": "Пропоную показати на карті: «{layer}» ({origin}).",
        "map_alt": "Ще можна показати: «{layer}» ({origin}).",
        "map_missing": "Для теми «{topic}» карти цього поля немає, тому я нічого не вмикаю.",
        "map_list": "Що можна показати на карті: {layers}.",
        "map_none": "Для цього поля карт немає.",
        "sim": "Порівняння сівозмін і прогноз урожаю рахує окрема програма, яку ще не підключено. "
        "Я **нічого не розраховував** і результатів не маю.",
        "sim_scenario": "Можу лише вибрати варіант «{scenario}» для майбутнього порівняння. Це ще не розрахунок.",
        "scenario_chip": "Варіант «{scenario}» (лише вибір)",
        "summary": "Ось що відомо про це поле:",
        "summary_layers": "На карті можна показати: {layers}.",
        "no_facts": "Про це поле ще немає даних, тож я не можу нічого сказати про ґрунт, воду чи погоду. "
        "Вони з’являться, коли надійдуть дані поля.",
        "topic": "Що відомо про поле на тему «{topic}»:",
        "topic_missing": "Даних на тему «{topic}» для цього поля немає. Я не вигадую значень, яких немає.",
        "unavailable": "немає даних",
        "fixture": "**демо-дані, не справжній вимір**",
        "cached": "збережено раніше",
        "unverified": "**дані від застосунку, сервіс даних їх не підтвердив**",
        "satellite_product": "{product}",
        "weather_data": "{product}",
        "ml_estimate": "комп’ютерна оцінка, не прямий вимір",
        "ml_short": "оцінка, не вимір",
        "ml_unvalidated": "точність ще не перевірена",
        "ml_validated": "точність перевірено на {n} вимірах",
        "deterministic_calc": "розрахунок за науковою формулою",
        "reference": "довідкове значення: {product}",
        "farmer_input": "з ваших слів",
        "chip_kind": {
            "satellite_product": "супутник",
            "deterministic_calc": "розрахунок",
            "reference": "довідник",
        },
        "irrigation_available": {False: "без поливу", True: "є полив"},
        "fertilizer_budget": {
            "low": "менші витрати на добрива",
            "minimize_n": "мінімум азотних добрив",
        },
        "topics": {
            "soil_moisture": "вологість ґрунту",
            "precipitation": "опади",
            "soil_ph": "кислотність ґрунту",
            "vegetation": "стан посівів",
            "temperature": "температура",
            "slope": "схил і рельєф",
        },
        "scenarios": {
            "typical": "типовий сезон",
            "drought_2024": "посуха 2024",
            "heat_plus_2_5c": "спека +2,5 °C",
        },
    },
    "en": {
        "banner": "_Demo mode: this reply follows ready-made rules, it is not AI._",
        "attachments": "Photos and voice messages are not processed yet.",
        "constraints": "From your words I understood these conditions. Confirm or remove them:",
        "constraints_known": "These conditions are already saved for the field, so I am not repeating them.",
        "apply_constraints": "Use these conditions in the comparison",
        "map_on": "I suggest showing on the map: “{layer}” ({origin}).",
        "map_alt": "I can also show: “{layer}” ({origin}).",
        "map_missing": "This field has no map for “{topic}”, so I am not changing the map.",
        "map_list": "What the map can show: {layers}.",
        "map_none": "This field has no maps.",
        "sim": "Rotation comparisons and harvest forecasts are calculated by a separate program that is not connected "
        "yet. I **did not calculate** anything and have no results.",
        "sim_scenario": "I can only choose the “{scenario}” case for a later comparison. That is not a calculation yet.",
        "scenario_chip": "“{scenario}” case (choice only)",
        "summary": "Here is what is known about this field:",
        "summary_layers": "The map can show: {layers}.",
        "no_facts": "There is no data about this field yet, so I cannot say anything about its soil, water or weather. "
        "It will appear once the field data arrives.",
        "topic": "What is known about the field on “{topic}”:",
        "topic_missing": "There is no data on “{topic}” for this field. I do not make up values that are not there.",
        "unavailable": "no data",
        "fixture": "**demo data, not a real measurement**",
        "cached": "saved earlier",
        "unverified": "**from the app, not confirmed by the data service**",
        "satellite_product": "{product}",
        "weather_data": "{product}",
        "ml_estimate": "computer estimate, not a direct measurement",
        "ml_short": "estimate, not a measurement",
        "ml_unvalidated": "accuracy not checked yet",
        "ml_validated": "accuracy checked against {n} measurements",
        "deterministic_calc": "calculated with a scientific formula",
        "reference": "reference value: {product}",
        "farmer_input": "from what you said",
        "chip_kind": {
            "satellite_product": "satellite",
            "deterministic_calc": "calculated",
            "reference": "reference",
        },
        "irrigation_available": {False: "no irrigation", True: "irrigation available"},
        "fertilizer_budget": {
            "low": "lower fertilizer costs",
            "minimize_n": "minimal nitrogen fertilizer",
        },
        "topics": {
            "soil_moisture": "soil moisture",
            "precipitation": "rainfall",
            "soil_ph": "soil acidity",
            "vegetation": "crop condition",
            "temperature": "temperature",
            "slope": "slope and terrain",
        },
        "scenarios": {
            "typical": "typical season",
            "drought_2024": "2024 drought",
            "heat_plus_2_5c": "heat +2.5 °C",
        },
    },
}

# --------------------------------------------------------------- formatting


def _origin(p: Provenance, lang: str) -> str:
    t = TEXT[lang]
    product = plain.source(p.source.product_id if p.source else None, lang) or "?"
    if p.kind == "ml_estimate" and p.model:
        parts = [t["ml_estimate"]]
        v = p.model.validation
        parts.append(t["ml_validated"].format(n=v.n) if v else t["ml_unvalidated"])
    elif p.kind == "deterministic_calc":
        parts = [t["deterministic_calc"]]
    elif p.kind in ("satellite_product", "reference", "weather_data"):
        parts = [t[p.kind].format(product=product)]
    else:
        parts = [t.get(p.kind, p.kind)]
    if p.data_status == "fixture":
        parts.append(t["fixture"])
    elif p.data_status == "cached":
        parts.append(t["cached"])
    return "; ".join(parts)


def _fact_line(f: Fact, lang: str, unverified: bool = False) -> str:
    if f.provenance.data_status == "unavailable":
        value = TEXT[lang]["unavailable"]
    elif f.quantity is not None:
        value = _quantity(f.quantity, lang)
    else:
        value = f.category or ""
    origin = _origin(f.provenance, lang) + (
        f"; {TEXT[lang]['unverified']}" if unverified else ""
    )
    return f"- **{_capital(plain.name(f.label_key, lang))}**: {value} — {origin}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _matches(data_keys: tuple[str, ...], *names: str) -> bool:
    return any(k in n.lower() for k in data_keys for n in names)


# --------------------------------------------------------------- reply composition


@dataclass
class Reply:
    lang: str
    # The rules agent has no data service: request facts are the app's assertions unless the host signed them.
    context_attested: bool = False
    paragraphs: list[str] = field(default_factory=list)
    facts: dict[str, Fact] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    actions: list[UIActionEnvelope] = field(default_factory=list)
    warnings: dict[str, AgentWarning] = field(default_factory=dict)
    tools: list[ToolName] = field(default_factory=list)

    def warn(self, code: str, detail: str) -> None:
        self.warnings.setdefault(code + detail, AgentWarning(code=code, detail=detail))

    def action(self, ctx: FieldContext, trigger: str, label: str, action) -> None:
        self.actions.append(
            UIActionEnvelope(
                id=f"a{len(self.actions) + 1}",
                field_context_revision=ctx.revision,
                trigger=trigger,
                label=label[:300],
                action=action,
            )
        )

    def relay(self, f: Fact) -> str:
        self.facts.setdefault(f.id, f)
        if f.provenance.data_status == "unavailable":
            src = f.provenance.source.product_id if f.provenance.source else f.id
            self.warn("source_unavailable", f"No value for {f.id} ({src}).")
        unverified = not self.context_attested and f.provenance.data_status in (
            "live",
            "cached",
        )
        if unverified:
            self.warn(
                "low_confidence",
                "Some values claim to be real measurements but were supplied by the app and "
                "not confirmed by a data service.",
            )
        return _fact_line(f, self.lang, unverified)


def _extract_constraints(text: str) -> list[Constraint]:
    """The farmer's explicit statements, kept only when their words carry the meaning (constraint_evidence)."""
    found: list[Constraint] = []
    if m := NO_IRRIGATION.search(text) or HAS_IRRIGATION.search(text):
        found.append(
            IrrigationConstraint(
                id="k_irrigation_available",
                type="irrigation_available",
                value=m.re is HAS_IRRIGATION,
                status="proposed",
                source="farmer_text",
                evidence=m.group(0),
            )
        )
    if m := REDUCE_NITROGEN.search(text) or REDUCE_FERTILIZER.search(text):
        found.append(
            FertilizerBudgetConstraint(
                id="k_fertilizer_budget",
                type="fertilizer_budget",
                value="minimize_n" if m.re is REDUCE_NITROGEN else "low",
                status="proposed",
                source="farmer_text",
                evidence=m.group(0),
            )
        )
    crops = constraint_evidence.crop_catalogue()
    return [
        c
        for c in found
        if not constraint_evidence.problem(
            c.type, c.value, c.evidence or "", text, explicit=True, known_crops=crops
        )
    ]


def _constraints(reply: Reply, ctx: FieldContext, text: str) -> None:
    t = TEXT[reply.lang]
    proposed = _extract_constraints(text)
    if not proposed:
        return
    reply.tools.append("propose_constraints")
    known = {(c.type, str(c.value)) for c in ctx.constraints if c.status != "rejected"}
    new = [c for c in proposed if (c.type, str(c.value)) not in known]
    if not new:
        reply.paragraphs.append(t["constraints_known"])
        return
    lines = [f"- {t[c.type][c.value]} — «{c.evidence}»" for c in new]
    reply.paragraphs.append(t["constraints"] + "\n" + "\n".join(lines))
    reply.constraints.extend(new)
    reply.action(
        ctx,
        "chip",
        t["apply_constraints"],
        SetConstraintsAction(type="compare.set_constraints", constraints=new),
    )


def _map(reply: Reply, ctx: FieldContext, topics: list[Topic]) -> None:
    t = TEXT[reply.lang]
    if not topics:
        names = ", ".join(
            f"«{plain.name(layer.label_key, reply.lang)}»"
            for layer in ctx.available_layers
        )
        reply.paragraphs.append(
            t["map_list"].format(layers=names) if names else t["map_none"]
        )
        return
    for topic in topics:
        layers = [
            la
            for la in ctx.available_layers
            if _matches(topic.data_keys, la.layer_id, la.label_key)
        ]
        if not layers:
            reply.paragraphs.append(
                t["map_missing"].format(topic=t["topics"][topic.key])
            )
            reply.warn(
                "source_unavailable",
                f"No map layer for topic '{topic.key}' in this field context.",
            )
            continue
        # Prefer the published product over our own model estimate; offer the others as chips.
        layers.sort(key=lambda la: la.provenance.kind == "ml_estimate")
        first, *others = layers
        if any(la.provenance.data_status == "fixture" for la in layers):
            reply.warn(
                "fixture_data", "Map layers proposed here are synthetic fixtures."
            )
        reply.tools.append("propose_ui_actions")
        reply.paragraphs.append(
            t["map_on"].format(
                layer=plain.name(first.label_key, reply.lang),
                origin=_origin(first.provenance, reply.lang),
            )
        )
        reply.action(
            ctx,
            "auto",
            _capital(plain.name(first.label_key, reply.lang)),
            SetLayersAction(
                type="map.set_layers", layer_ids=[first.layer_id], mode="replace"
            ),
        )
        for layer in others:
            reply.paragraphs.append(
                t["map_alt"].format(
                    layer=plain.name(layer.label_key, reply.lang),
                    origin=_origin(layer.provenance, reply.lang),
                )
            )
            # Chip labels are plain button text: no markdown, short origin hint only.
            kind = layer.provenance.kind
            hint = (
                t["ml_short"]
                if kind == "ml_estimate"
                else t["chip_kind"].get(kind, kind.replace("_", " "))
            )
            name = _capital(plain.name(layer.label_key, reply.lang))
            said = (
                kind == "ml_estimate" and t["ml_short"].split(",")[0] in name.lower()
            )  # "(оцінка)" already in the name
            reply.action(
                ctx,
                "chip",
                name if said else f"{name} ({hint})",
                SetLayersAction(
                    type="map.set_layers", layer_ids=[layer.layer_id], mode="replace"
                ),
            )


def _simulation(reply: Reply, ctx: FieldContext, text: str) -> None:
    t = TEXT[reply.lang]
    reply.paragraphs.append(t["sim"])
    reply.warn(
        "source_unavailable",
        "Rotation simulation service is not connected in M1; nothing was simulated.",
    )
    scenario = next((sid for sid, rx in SCENARIOS if rx.search(text)), None)
    if scenario and scenario != ctx.ui_state.scenario:
        name = t["scenarios"][scenario]
        reply.paragraphs.append(t["sim_scenario"].format(scenario=name))
        reply.action(
            ctx,
            "chip",
            t["scenario_chip"].format(scenario=name),
            SetScenarioAction(type="scenario.set", scenario=scenario),
        )


def _topic_facts(reply: Reply, ctx: FieldContext, topics: list[Topic]) -> None:
    t = TEXT[reply.lang]
    for topic in topics:
        facts = [f for f in ctx.facts if _matches(topic.data_keys, f.label_key)]
        name = t["topics"][topic.key]
        if not facts:
            reply.paragraphs.append(t["topic_missing"].format(topic=name))
            reply.warn(
                "source_unavailable",
                f"No fact for topic '{topic.key}' in this field context.",
            )
            continue
        reply.tools.append("get_field_facts")
        reply.paragraphs.append(
            t["topic"].format(topic=name)
            + "\n"
            + "\n".join(reply.relay(f) for f in facts)
        )


def _summary(reply: Reply, ctx: FieldContext) -> None:
    t = TEXT[reply.lang]
    if not ctx.facts:
        reply.paragraphs.append(t["no_facts"])
        reply.warn("source_unavailable", "The field context contains no facts.")
        return
    reply.tools.append("get_field_facts")
    reply.paragraphs.append(
        t["summary"] + "\n" + "\n".join(reply.relay(f) for f in ctx.facts)
    )
    if ctx.available_layers:
        names = ", ".join(
            f"«{plain.name(layer.label_key, reply.lang)}»"
            for layer in ctx.available_layers
        )
        reply.paragraphs.append(t["summary_layers"].format(layers=names))


def compose(request: AgentRequest, context_attested: bool = False) -> Reply:
    """Pure function of the request: same input, same reply."""
    ctx = request.field_context
    text = request.message.text or ""
    reply = Reply(lang=_language(request), context_attested=context_attested)
    reply.paragraphs.append(TEXT[reply.lang]["banner"])
    if request.message.attachments:
        reply.paragraphs.append(TEXT[reply.lang]["attachments"])
        reply.warn(
            "source_unavailable", "Attachment processing is not implemented in M1."
        )

    topics = [tp for tp in TOPICS if any(re.search(p, text, _I) for p in tp.patterns)]
    # "Show soil moisture" is a map request when this field has a layer for it; otherwise the facts answer it.
    shows_layer = bool(SHOW_INTENT.search(text)) and any(
        _matches(tp.data_keys, la.layer_id, la.label_key)
        for tp in topics
        for la in ctx.available_layers
    )
    wants_map = bool(MAP_INTENT.search(text)) or shows_layer
    wants_simulation = bool(SIMULATION_INTENT.search(text))
    before = len(reply.paragraphs)

    _constraints(reply, ctx, text)
    if wants_map:
        _map(reply, ctx, topics)
    if wants_simulation:
        _simulation(reply, ctx, text)
    if topics and not wants_map:
        _topic_facts(reply, ctx, topics)
    if len(reply.paragraphs) == before and text:
        # No rule matched. A question about the field gets its summary; anything else gets what this demo can do
        # (it cannot tell a farming question from an unrelated one, so it never calls anything off-topic).
        if scope.asks_about_the_field(text):
            _summary(reply, ctx)
        else:
            reply.paragraphs.append(scope.DEMO_LIMITS[reply.lang])

    if any(f.provenance.data_status == "fixture" for f in reply.facts.values()):
        reply.warn(
            "fixture_data",
            "Values relayed from the field context are synthetic fixtures.",
        )
    return reply


class DeterministicDevAgent:
    name = NAME

    async def respond(
        self, request: AgentRequest, turn_id: str, trust: RequestTrust = UNTRUSTED
    ) -> AsyncIterator[AgentOutput]:
        reply = compose(request, trust.attested)
        for i, tool in enumerate(dict.fromkeys(reply.tools), start=1):
            yield ToolStartedEvent(event="tool.started", call_id=f"call_{i}", tool=tool)
            yield ToolFinishedEvent(
                event="tool.finished", call_id=f"call_{i}", status="ok", latency_ms=0
            )
        markdown = "\n\n".join(reply.paragraphs)
        for chunk in _chunks(markdown):
            yield TextDeltaEvent(event="text.delta", text=chunk)
        yield AgentMessage(
            turn_id=turn_id,
            locale=reply.lang,
            blocks=[TextBlock(type="text", markdown=markdown)],
            facts=list(reply.facts.values()),
            constraints_proposed=reply.constraints,
            ui_actions=reply.actions,
            warnings=list(reply.warnings.values()),
            meta=ResponseMeta(
                provider=NAME,
                model_id="none",
                prompt_version=RULES_VERSION,
                latency_ms=0,
            ),
        )
