"""Field Shift integration contracts, v0.1. This file is the source of truth.

The JSON Schema is generated with `python -m contracts.export`, and the
TypeScript types with `npm run gen:ts`. Field semantics: CONTRACTS.md.
"""

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, TypeAliasType, Union

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = "0.1"


class Model(BaseModel):
    # Contracts travel as JSON between teams: no unknown keys, no silent coercion.
    model_config = ConfigDict(extra="forbid", strict=True)


# ---------------------------------------------------------------- primitives

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
I18nKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")]
CropId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Locale = Literal["uk", "en"]


def _require_utc(v: datetime) -> datetime:
    if v.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC (offset Z / +00:00)")
    return v


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]

# UCUM codes. ponytail: an allowlist, not a UCUM parser. Add a code here when a
# new unit is needed; "Cel.d" (degree-days) is a common extension.
# Money (UCUM has none): ISO 4217 codes, alone or per hectare; only the two currencies the farm-budget calculator
# accepts, never converted into each other (added 2026-11-14).
Unit = Literal[
    "1", "%", "[pH]", "mm", "mm/d", "m3/m3", "Cel", "Cel.d", "d", "m", "cm",
    "ha", "deg", "kg/ha", "t/ha", "g/kg", "cmol/kg", "MJ/m2/d", "m/s",
    "UAH", "USD", "UAH/ha", "USD/ha",
]


class TimeRange(Model):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self):
        if self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class Uncertainty(Model):
    kind: Literal["sd", "ci95", "range", "qualitative"]
    low: FiniteFloat | None = None
    high: FiniteFloat | None = None
    sd: Annotated[FiniteFloat, Field(ge=0)] | None = None
    note: ShortText | None = None

    @model_validator(mode="after")
    def _shape(self):
        if self.kind in ("ci95", "range"):
            if self.low is None or self.high is None or self.low > self.high:
                raise ValueError(f"uncertainty '{self.kind}' needs low <= high")
        elif self.kind == "sd" and self.sd is None:
            raise ValueError("uncertainty 'sd' needs sd")
        elif self.kind == "qualitative" and self.note is None:
            raise ValueError("uncertainty 'qualitative' needs note")
        return self


class Quantity(Model):
    value: FiniteFloat
    unit: Unit
    uncertainty: Uncertainty | None = None


# ---------------------------------------------------------------- provenance

ProvenanceKind = Literal[
    "satellite_product",   # published EO / reanalysis product, as distributed
    "ml_estimate",         # output of a Field Shift ML model
    "deterministic_calc",  # formula applied to other values (e.g. FAO-56)
    "reference",           # value taken from a document or table
    "farmer_input",        # stated, clicked or photographed by the farmer
    "llm_explanation",     # prose from the language model; never a number source
    "weather_data",        # a weather forecast or weather archive (reanalysis) as distributed by its provider (added 2026-11-10)
]
DataStatus = Literal["live", "cached", "fixture", "unavailable"]


class Source(Model):
    provider: ShortText    # "NASA NSIDC DAAC", "ISRIC", "FAO"
    product_id: ShortText  # "SPL4SMGP", "GPM_3IMERGDF", "FAO-56"
    version: ShortText     # "008"; "unverified" when not yet confirmed
    level: ShortText | None = None
    doi: ShortText | None = None
    url: HttpUrl | None = None


class Validation(Model):
    metric: ShortText  # "RMSE vs ISMN stations"
    value: FiniteFloat
    n: Annotated[int, Field(ge=1)]
    note: ShortText | None = None


class ModelInfo(Model):
    name: ShortText
    version: ShortText
    validation: Validation | None = None  # None = not validated; the UI must say so


class Spatial(Model):
    resolution_m: Annotated[FiniteFloat, Field(gt=0)]
    note: ShortText | None = None


class Temporal(TimeRange):
    aggregation: ShortText | None = None  # "monthly_mean", "sum", "daily"


class Provenance(Model):
    kind: ProvenanceKind
    data_status: DataStatus
    source: Source | None = None
    method: ShortText | None = None
    model: ModelInfo | None = None
    inputs: list[Id] = []
    spatial: Spatial | None = None
    temporal: Temporal | None = None
    retrieved_at: UtcDatetime

    @model_validator(mode="after")
    def _kind_requirements(self):
        if self.kind in ("satellite_product", "reference", "weather_data") and self.source is None:
            raise ValueError(f"provenance kind '{self.kind}' requires source")
        if self.kind == "ml_estimate" and self.model is None:
            raise ValueError("provenance kind 'ml_estimate' requires model")
        if self.kind == "deterministic_calc" and self.method is None:
            raise ValueError("provenance kind 'deterministic_calc' requires method")
        return self


Domain = Literal["soil", "water", "climate", "vegetation", "terrain", "farm", "rotation"]
CALC_FACT_PREFIX = "calc."  # facts of the agent's calculate_farm_budget tool; never in a FieldContext


class Fact(Model):
    id: Id
    domain: Domain
    label_key: I18nKey
    quantity: Quantity | None = None
    category: ShortText | None = None  # categorical value, e.g. hydrologic group "C"
    depth_cm: tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(gt=0)]] | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _value_rules(self):
        has_value = (self.quantity is not None) + (self.category is not None)
        if self.provenance.data_status == "unavailable":
            if has_value:
                raise ValueError("an unavailable fact must not carry a value")
        elif has_value != 1:
            raise ValueError("a fact needs exactly one of quantity or category")
        if self.provenance.kind == "llm_explanation" and self.quantity is not None:
            raise ValueError("LLM output cannot be the source of a numeric fact")
        if self.depth_cm is not None and self.depth_cm[0] >= self.depth_cm[1]:
            raise ValueError("depth_cm must be (top, bottom) with top < bottom")
        return self


def _require_unique(what: str, ids: list[str]) -> None:
    if dupes := [i for i, n in Counter(ids).items() if n > 1]:
        raise ValueError(f"duplicate {what} ids: {sorted(dupes)}")


# ---------------------------------------------------------------- geometry (WGS84, [lon, lat])

Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
LonLat = tuple[Lon, Lat]


class Point(Model):
    type: Literal["Point"]
    coordinates: LonLat


class Polygon(Model):
    type: Literal["Polygon"]
    coordinates: Annotated[list[Annotated[list[LonLat], Field(min_length=4)]], Field(min_length=1)]

    @model_validator(mode="after")
    def _closed(self):
        for ring in self.coordinates:
            if ring[0] != ring[-1]:
                raise ValueError("polygon rings must be closed (first == last position)")
        return self


Geometry = Annotated[Union[Point, Polygon], Field(discriminator="type")]


# ---------------------------------------------------------------- farmer constraints

ConstraintType = Literal[
    "irrigation_available", "fertilizer_budget", "erosion_protection",
    "rotation_length_years", "excluded_crops", "preferred_crops", "previous_crop",
    "organic_only", "machinery_limit", "market_preference",
]


class _ConstraintBase(Model):
    id: Id
    status: Literal["proposed", "confirmed", "rejected"]
    source: Literal["farmer_text", "farmer_voice", "farmer_ui", "agent_inferred"]
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    confidence: Annotated[FiniteFloat, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def _traceable(self):
        if self.source in ("farmer_text", "farmer_voice") and self.evidence is None:
            raise ValueError("a constraint from farmer text/voice needs the evidence span")
        if self.source == "agent_inferred" and self.confidence is None:
            raise ValueError("an agent-inferred constraint needs confidence")
        return self


CropList = Annotated[list[CropId], Field(min_length=1)]


class IrrigationConstraint(_ConstraintBase):
    type: Literal["irrigation_available"]
    value: bool


class FertilizerBudgetConstraint(_ConstraintBase):
    type: Literal["fertilizer_budget"]
    value: Literal["minimize_n", "low", "normal"]


class ErosionProtectionConstraint(_ConstraintBase):
    type: Literal["erosion_protection"]
    value: bool


class RotationLengthConstraint(_ConstraintBase):
    type: Literal["rotation_length_years"]
    value: Annotated[int, Field(ge=2, le=6)]


class ExcludedCropsConstraint(_ConstraintBase):
    type: Literal["excluded_crops"]
    value: CropList


class PreferredCropsConstraint(_ConstraintBase):
    type: Literal["preferred_crops"]
    value: CropList


class PreviousCropConstraint(_ConstraintBase):
    type: Literal["previous_crop"]
    value: CropId


class OrganicOnlyConstraint(_ConstraintBase):
    type: Literal["organic_only"]
    value: bool


class MachineryLimitConstraint(_ConstraintBase):
    type: Literal["machinery_limit"]
    value: ShortText  # free text, display only; never scored


class MarketPreferenceConstraint(_ConstraintBase):
    type: Literal["market_preference"]
    value: CropList


Constraint = TypeAliasType(
    "Constraint",
    Annotated[
        Union[
            IrrigationConstraint, FertilizerBudgetConstraint, ErosionProtectionConstraint,
            RotationLengthConstraint, ExcludedCropsConstraint, PreferredCropsConstraint,
            PreviousCropConstraint, OrganicOnlyConstraint, MachineryLimitConstraint,
            MarketPreferenceConstraint,
        ],
        Field(discriminator="type"),
    ],
)

# Owned by member 2. driest/hottest_recent_year: the driest / hottest season of the service's recent archive for
# that field, replayed as it was (the Marsden service, 2026-11-12); not "drought 2024" nor "+2.5 °C".
ScenarioId = Literal["typical", "drought_2024", "heat_plus_2_5c", "driest_recent_year", "hottest_recent_year"]


# ---------------------------------------------------------------- field context

class Region(Model):
    country_iso2: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    admin1: ShortText | None = None


class TimeseriesInfo(Model):
    metric_id: Id
    label_key: I18nKey
    unit: Unit
    range: TimeRange
    provenance: Provenance


class MapLayer(Model):
    layer_id: Id
    label_key: I18nKey
    kind: Literal["raster", "vector", "3d"]
    provenance: Provenance


class ChartInfo(Model):
    chart_id: Id
    metric_ids: Annotated[list[Id], Field(min_length=1)]


class CropSeason(Model):
    season: Annotated[str, StringConstraints(pattern=r"^\d{4}(-\d{4})?$")]  # "2025" or "2024-2025"
    crop_id: CropId
    source: Literal["farmer_input", "inferred"]


class ChartFocus(Model):
    chart_id: Id
    metric_ids: Annotated[list[Id], Field(min_length=1)]
    highlight: TimeRange | None = None


class UIState(Model):
    active_layer_ids: list[Id] = []
    time_range: TimeRange | None = None
    chart_focus: ChartFocus | None = None
    selected_rotation_ids: list[Id] = []
    scenario: ScenarioId = "typical"
    screen_id: Id | None = None  # the app screen on display (AppScreen.screen_id), when the host has screens


class FieldProfile(Model):
    """What the geo service (member 1) returns for a field; the host merges it into FieldContext."""

    field_id: Id
    facts: list[Fact]
    timeseries_index: list[TimeseriesInfo] = []
    available_layers: list[MapLayer] = []

    @model_validator(mode="after")
    def _unique(self):
        _require_unique("fact", [f.id for f in self.facts])
        _require_unique("metric", [t.metric_id for t in self.timeseries_index])
        _require_unique("layer", [layer.layer_id for layer in self.available_layers])
        return self


class FieldContext(Model):
    """Shared state for the selected field. Owned by the host app (member 5)."""

    schema_version: Literal["0.1"]
    field_id: Id
    revision: Annotated[int, Field(ge=0)]  # bump on every change; stale UI actions are dropped
    geometry: Geometry
    centroid: LonLat
    area_ha: Annotated[FiniteFloat, Field(gt=0)] | None = None
    region: Region
    facts: list[Fact] = []
    timeseries_index: list[TimeseriesInfo] = []
    available_layers: list[MapLayer] = []
    available_charts: list[ChartInfo] = []
    crop_history: list[CropSeason] = []
    constraints: list[Constraint] = []
    ui_state: UIState = Field(default_factory=UIState)
    locale: Locale

    @model_validator(mode="after")
    def _references(self):
        _require_unique("fact", [f.id for f in self.facts])
        # "calc." ids are the agent's own farm-budget arithmetic of one turn: a context fact must not pose as one.
        if reserved := sorted(f.id for f in self.facts if f.id.startswith(CALC_FACT_PREFIX)):
            raise ValueError(f"fact ids starting with '{CALC_FACT_PREFIX}' are reserved for the agent: {reserved}")
        _require_unique("metric", [t.metric_id for t in self.timeseries_index])
        _require_unique("layer", [layer.layer_id for layer in self.available_layers])
        _require_unique("chart", [c.chart_id for c in self.available_charts])
        _require_unique("constraint", [c.id for c in self.constraints])
        layers = {layer.layer_id for layer in self.available_layers}
        metrics = {t.metric_id for t in self.timeseries_index}
        charts = {c.chart_id: set(c.metric_ids) for c in self.available_charts}
        if unknown := set(self.ui_state.active_layer_ids) - layers:
            raise ValueError(f"ui_state references unknown layer ids: {sorted(unknown)}")
        for chart in self.available_charts:
            if unknown := set(chart.metric_ids) - metrics:
                raise ValueError(f"chart '{chart.chart_id}' references unknown metric ids: {sorted(unknown)}")
        if focus := self.ui_state.chart_focus:
            if focus.chart_id not in charts:
                raise ValueError(f"chart_focus references unknown chart id '{focus.chart_id}'")
            if unknown := set(focus.metric_ids) - charts[focus.chart_id]:
                raise ValueError(f"chart_focus references unknown metric ids: {sorted(unknown)}")
        return self


# ---------------------------------------------------------------- UI actions (widget -> host)

class SetLayersAction(Model):
    type: Literal["map.set_layers"]
    layer_ids: Annotated[list[Id], Field(min_length=1)]
    mode: Literal["replace", "add"]


class FocusFieldAction(Model):
    type: Literal["map.focus_field"]


class SetTimeRangeAction(TimeRange):
    type: Literal["time.set_range"]


class FocusChartAction(ChartFocus):
    type: Literal["chart.focus"]


class SetConstraintsAction(Model):
    type: Literal["compare.set_constraints"]
    constraints: list[Constraint]


class SelectRotationsAction(Model):
    type: Literal["compare.select_rotations"]
    rotation_ids: Annotated[list[Id], Field(min_length=1)]


class SetScenarioAction(Model):
    type: Literal["scenario.set"]
    scenario: ScenarioId


class OpenPanelAction(Model):
    type: Literal["panel.open"]
    panel: Literal["soil_portrait", "comparison", "calendar", "citations"]
    ref_id: Id | None = None
    # comparison panel only (M6C): the scenario of the referenced result, set by the agent server from the
    # verified result (never by the model). The host must refuse a fetched result for another scenario.
    scenario: ScenarioId | None = None


class OpenScreenAction(Model):
    """Show one of the app's own screens (AppScreen.screen_id from the host's capabilities). View only."""
    type: Literal["app.open_screen"]
    screen_id: Id


UIAction = TypeAliasType(
    "UIAction",
    Annotated[
        Union[
            SetLayersAction, FocusFieldAction, SetTimeRangeAction, FocusChartAction,
            SetConstraintsAction, SelectRotationsAction, SetScenarioAction, OpenPanelAction, OpenScreenAction,
        ],
        Field(discriminator="type"),
    ],
)

UIActionType = Literal[
    "map.set_layers", "map.focus_field", "time.set_range", "chart.focus", "compare.set_constraints",
    "compare.select_rotations", "scenario.set", "panel.open", "app.open_screen",
]


class UIActionEnvelope(Model):
    id: Id
    field_context_revision: Annotated[int, Field(ge=0)]
    trigger: Literal["auto", "chip"]  # auto = host applies now; chip = rendered as a button
    label: ShortText                  # localized chip text
    reason: ShortText | None = None
    action: UIAction


# ---------------------------------------------------------------- citations

class Locator(Model):
    chapter: ShortText | None = None
    section: ShortText | None = None
    table: ShortText | None = None
    page: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _not_empty(self):
        if not any((self.chapter, self.section, self.table, self.page)):
            raise ValueError("locator needs at least one of chapter, section, table, page")
        return self


class Citation(Model):
    id: Id
    chunk_id: Id
    doc_id: Id
    title: ShortText
    publisher: ShortText
    year: Annotated[int, Field(ge=1900, le=2100)]
    edition: ShortText | None = None
    locator: Locator
    url: HttpUrl  # always from corpus metadata, never from model text
    quote: ShortText  # exact span of the retrieved chunk
    language: Locale
    licence: ShortText


# ---------------------------------------------------------------- agent request / response

class Attachment(Model):
    attachment_id: Id
    kind: Literal["image", "audio"]


class UserMessage(Model):
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    attachments: Annotated[list[Attachment], Field(max_length=4)] = []

    @model_validator(mode="after")
    def _not_empty(self):
        if self.text is None and not self.attachments:
            raise ValueError("message needs text or attachments")
        return self


class HistoryTurn(Model):
    role: Literal["user", "assistant"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=16000)]


class AppScreen(Model):
    """A screen of the host app the assistant may open or point to. Host-declared UI data, not permission."""
    screen_id: Id
    label: ShortText                    # the screen's name as the farmer sees it
    how_to_open: ShortText | None = None  # plain words for finding it by hand, e.g. "Menu → Fields"


class ClientCapabilities(Model):
    """What the host app can show or do right now (HostCapabilities of the widget). Unknown when absent."""
    actions: Annotated[list[UIActionType], Field(max_length=20)] = []
    screens: Annotated[list[AppScreen], Field(max_length=30)] = []

    @model_validator(mode="after")
    def _unique(self):
        _require_unique("screen", [x.screen_id for x in self.screens])
        return self


class ClientInfo(Model):
    widget_version: ShortText
    capabilities: ClientCapabilities | None = None  # added 2026-11-10; absent = unknown (older widgets)


class AgentRequest(Model):
    request_id: Id
    session_id: Id
    locale: Literal["uk", "en", "auto"]
    message: UserMessage
    history: Annotated[list[HistoryTurn], Field(max_length=10)] = []
    field_context: FieldContext
    client: ClientInfo


class TextBlock(Model):
    type: Literal["text"]
    markdown: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class ClarifyingQuestionBlock(Model):
    type: Literal["clarifying_question"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    options: Annotated[list[ShortText], Field(max_length=6)] = []


# Presentation blocks (v0.2, additive). They never carry a number of their own: every value is a reference to a
# fact in AgentMessage.facts, which the client renders with its unit and origin. Labels are plain words.

class MetricsBlock(Model):
    """A row of value cards: several values of this field at a glance."""
    type: Literal["metrics"]
    title: ShortText | None = None
    fact_ids: Annotated[list[Id], Field(min_length=1, max_length=6)]
    # Set by the agent server from verified results, never by the model (M6C): the scenario of the simulation
    # values this block shows. A block never mixes scenarios; a block without simulation values has none.
    scenario: ScenarioId | None = None


class TableRow(Model):
    label: ShortText
    fact_ids: Annotated[list[Id | None], Field(min_length=1, max_length=5)]  # one per column; None = no value


class TableBlock(Model):
    """Options side by side (rows) on the same measures (columns)."""
    type: Literal["table"]
    title: ShortText | None = None
    columns: Annotated[list[ShortText], Field(min_length=1, max_length=5)]
    rows: Annotated[list[TableRow], Field(min_length=1, max_length=10)]
    # Set by the agent server from verified results, never by the model (M6C): the scenario of the simulation
    # values this block shows. A block never mixes scenarios; a block without simulation values has none.
    scenario: ScenarioId | None = None

    @model_validator(mode="after")
    def _shape(self):
        if any(len(r.fact_ids) != len(self.columns) for r in self.rows):
            raise ValueError("every table row needs one fact id (or null) per column")
        return self


class ChartBar(Model):
    label: ShortText
    fact_id: Id


class BarChartBlock(Model):
    """One measure compared across options; all bars share one unit."""
    type: Literal["bar_chart"]
    title: ShortText
    bars: Annotated[list[ChartBar], Field(min_length=2, max_length=8)]
    # Set by the agent server from verified results, never by the model (M6C): the scenario of the simulation
    # values this block shows. A block never mixes scenarios; a block without simulation values has none.
    scenario: ScenarioId | None = None


class CalloutBlock(Model):
    """A short notice set apart from the text (a caution, or something to know)."""
    type: Literal["callout"]
    tone: Literal["info", "warning"]
    text: ShortText
    fact_ids: Annotated[list[Id], Field(max_length=3)] = []
    # Set by the agent server from verified results, never by the model (M6C): the scenario of the simulation
    # values this block shows. A block never mixes scenarios; a block without simulation values has none.
    scenario: ScenarioId | None = None


MessageBlock = TypeAliasType(
    "MessageBlock",
    Annotated[Union[TextBlock, ClarifyingQuestionBlock, MetricsBlock, TableBlock, BarChartBlock, CalloutBlock],
              Field(discriminator="type")],
)


def block_fact_ids(block: Any) -> list[str]:
    """Fact ids a presentation block shows, in display order."""
    if isinstance(block, (MetricsBlock, CalloutBlock)):
        return list(block.fact_ids)
    if isinstance(block, TableBlock):
        return [fid for r in block.rows for fid in r.fact_ids if fid]
    if isinstance(block, BarChartBlock):
        return [b.fact_id for b in block.bars]
    return []


_SCENARIO_IN_METHOD = re.compile(r"scenario=([a-z0-9_]+)")


def simulation_scenarios(facts: list["Fact"]) -> set[str]:
    """Scenarios of the simulation outputs among `facts` (compare_rotations scores record theirs in the method)."""
    return {m.group(1) for f in facts
            if f.domain == "rotation" and f.id.startswith("sim.") and (m := _SCENARIO_IN_METHOD.search(f.provenance.method or ""))}


class AgentWarning(Model):
    code: Literal["ungrounded_number", "source_unavailable", "fixture_data", "low_confidence"]
    detail: ShortText | None = None


class ResponseMeta(Model):
    provider: ShortText
    model_id: ShortText
    prompt_version: ShortText
    latency_ms: Annotated[int, Field(ge=0)]


class AgentMessage(Model):
    turn_id: Id
    locale: Locale
    blocks: Annotated[list[MessageBlock], Field(min_length=1)]
    facts: list[Fact] = []          # every number shown in blocks is one of these
    citations: list[Citation] = []
    constraints_proposed: list[Constraint] = []
    ui_actions: list[UIActionEnvelope] = []
    warnings: list[AgentWarning] = []
    meta: ResponseMeta

    @model_validator(mode="after")
    def _consistency(self):
        _require_unique("fact", [f.id for f in self.facts])
        _require_unique("citation", [c.id for c in self.citations])
        _require_unique("ui action", [a.id for a in self.ui_actions])
        facts = {f.id: f for f in self.facts}
        for block in self.blocks:
            missing = [fid for fid in block_fact_ids(block) if fid not in facts]
            if missing:
                raise ValueError(f"block '{block.type}' shows facts not in the message: {missing}")
            if hasattr(block, "scenario"):
                shown = simulation_scenarios([facts[fid] for fid in block_fact_ids(block)])
                if len(shown) > 1 or block.scenario != next(iter(shown), None):
                    raise ValueError(f"block '{block.type}' must name the one scenario of its simulation values")
            if isinstance(block, BarChartBlock):
                quantities = [facts[b.fact_id].quantity for b in block.bars]
                if any(q is None for q in quantities) or len({q.unit for q in quantities}) != 1:
                    raise ValueError("bar chart bars need numeric facts with one shared unit")
        if any(f.provenance.data_status == "fixture" for f in self.facts) and not any(
            w.code == "fixture_data" for w in self.warnings
        ):
            raise ValueError("a message that shows fixture facts must carry a 'fixture_data' warning")
        return self


ErrorCode = Literal[
    "invalid_request", "rate_limited", "upstream_timeout", "upstream_unavailable",
    "llm_error", "payload_too_large", "internal",
]


class ErrorBody(Model):
    code: ErrorCode
    message_key: I18nKey
    retryable: bool
    request_id: Id | None = None
    detail: ShortText | None = None


class ErrorEnvelope(Model):
    """Body of every non-2xx HTTP response from the agent service."""

    error: ErrorBody


ToolName = Literal[
    "get_field_facts", "get_timeseries", "compare_rotations", "search_knowledge",
    "propose_constraints", "propose_ui_actions", "present_blocks", "get_weather_forecast", "get_season_weather", "check_sowing_soil", "analyze_soil_photo",
    "calculate_farm_budget",
]


class TurnStartedEvent(Model):
    event: Literal["turn.started"]
    turn_id: Id


class TextDeltaEvent(Model):
    event: Literal["text.delta"]
    text: Annotated[str, StringConstraints(min_length=1)]


class ToolStartedEvent(Model):
    event: Literal["tool.started"]
    call_id: Id
    tool: ToolName


class ToolFinishedEvent(Model):
    event: Literal["tool.finished"]
    call_id: Id
    status: Literal["ok", "error", "timeout"]
    latency_ms: Annotated[int, Field(ge=0)]


class TurnCompletedEvent(Model):
    event: Literal["turn.completed"]
    message: AgentMessage


class ErrorEvent(ErrorBody):
    event: Literal["error"]


AgentEvent = TypeAliasType(
    "AgentEvent",
    Annotated[
        Union[TurnStartedEvent, TextDeltaEvent, ToolStartedEvent, ToolFinishedEvent,
              TurnCompletedEvent, ErrorEvent],
        Field(discriminator="event"),
    ],
)


# ---------------------------------------------------------------- geo + simulation services

class TimeseriesQuery(Model):
    """Agent -> geo service (member 1)."""

    field_id: Id
    metric_id: Id
    range: TimeRange
    aggregation: Literal["daily", "dekad", "monthly"]


class Timeseries(Model):
    metric_id: Id
    unit: Unit
    aggregation: Literal["daily", "dekad", "monthly"]
    points: list[tuple[date, FiniteFloat | None]]  # null = gap (cloud, missing granule)
    provenance: Provenance

    @model_validator(mode="after")
    def _sorted(self):
        days = [p[0] for p in self.points]
        if any(a >= b for a, b in zip(days, days[1:])):
            raise ValueError("timeseries points must be strictly increasing in date")
        return self


class CompareRotationsRequest(Model):
    """Agent or host -> simulation service (member 2)."""

    field_id: Id
    field_context_revision: Annotated[int, Field(ge=0)]
    constraints: list[Constraint] = []  # the service ignores status == "rejected"
    scenario: ScenarioId
    rotation_ids: list[Id] = []         # empty = let the service choose candidates


class Score(Quantity):
    provenance: Provenance

    @model_validator(mode="after")
    def _computed(self):
        if self.provenance.kind not in ("deterministic_calc", "ml_estimate"):
            raise ValueError("rotation scores must be deterministic_calc or ml_estimate")
        return self


class RotationScores(Model):
    """A service sends only what it computes: an absent score is "not computed", never zero."""
    water_risk: Score                       # share of water need or of days short of water: the method says which
    soil_cover_days: Score | None = None
    n_input: Score | None = None
    yield_risk: Score | None = None         # probability of a harvest loss beyond a threshold
    yield_loss_water: Score | None = None   # size of the harvest loss caused by water shortage (not a probability)


class RotationStep(Model):
    year_index: Annotated[int, Field(ge=1, le=10)]
    season: Literal["winter", "spring", "summer", "cover"]
    crop_id: CropId


class RotationOption(Model):
    rotation_id: Id
    sequence: Annotated[list[RotationStep], Field(min_length=1)]
    scores: RotationScores
    rank: Annotated[int, Field(ge=1)]
    reason_fact_ids: list[Id] = []


class RotationComparison(Model):
    scenario: ScenarioId
    rotations: Annotated[list[RotationOption], Field(min_length=1)]
    facts: list[Fact] = []
    constraint_weights_applied: dict[ConstraintType, FiniteFloat] = {}  # from the deterministic mapping

    @model_validator(mode="after")
    def _consistency(self):
        _require_unique("rotation", [r.rotation_id for r in self.rotations])
        _require_unique("fact", [f.id for f in self.facts])
        if len({r.rank for r in self.rotations}) != len(self.rotations):
            raise ValueError("rotation ranks must be unique")
        known = {f.id for f in self.facts}
        for r in self.rotations:
            if unknown := set(r.reason_fact_ids) - known:
                raise ValueError(f"rotation '{r.rotation_id}' cites unknown fact ids: {sorted(unknown)}")
        return self
