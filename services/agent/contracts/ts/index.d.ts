/* Generated from contracts/schema/fieldshift-contracts.schema.json. Do not edit; run: npm run gen:ts */

export type Constraint =
  | IrrigationConstraint
  | FertilizerBudgetConstraint
  | ErosionProtectionConstraint
  | RotationLengthConstraint
  | ExcludedCropsConstraint
  | PreferredCropsConstraint
  | PreviousCropConstraint
  | OrganicOnlyConstraint
  | MachineryLimitConstraint
  | MarketPreferenceConstraint;
export type AgentEvent =
  TurnStartedEvent | TextDeltaEvent | ToolStartedEvent | ToolFinishedEvent | TurnCompletedEvent | ErrorEvent;
export type MessageBlock =
  TextBlock | ClarifyingQuestionBlock | MetricsBlock | TableBlock | BarChartBlock | CalloutBlock;
export type UIAction =
  | SetLayersAction
  | FocusFieldAction
  | SetTimeRangeAction
  | FocusChartAction
  | SetConstraintsAction
  | SelectRotationsAction
  | SetScenarioAction
  | OpenPanelAction
  | OpenScreenAction;

/**
 * Index of the top-level contract types. Export root only; never sent over the wire.
 */
export interface FieldShiftContracts {
  field_context: FieldContext;
  field_profile: FieldProfile;
  agent_request: AgentRequest;
  agent_event: AgentEvent;
  agent_message: AgentMessage;
  ui_action_envelope: UIActionEnvelope;
  error_envelope: ErrorEnvelope;
  timeseries_query: TimeseriesQuery;
  timeseries: Timeseries;
  compare_rotations_request: CompareRotationsRequest;
  rotation_comparison: RotationComparison;
}
/**
 * Shared state for the selected field. Owned by the host app (member 5).
 */
export interface FieldContext {
  schema_version: "0.1";
  field_id: string;
  revision: number;
  geometry: Point | Polygon;
  /**
   * @minItems 2
   * @maxItems 2
   */
  centroid: [number, number];
  area_ha?: number | null;
  region: Region;
  facts?: Fact[];
  timeseries_index?: TimeseriesInfo[];
  available_layers?: MapLayer[];
  available_charts?: ChartInfo[];
  crop_history?: CropSeason[];
  constraints?: Constraint[];
  ui_state?: UIState;
  locale: "uk" | "en";
}
export interface Point {
  type: "Point";
  /**
   * @minItems 2
   * @maxItems 2
   */
  coordinates: [number, number];
}
export interface Polygon {
  type: "Polygon";
  /**
   * @minItems 1
   */
  coordinates: [number, number][][];
}
export interface Region {
  country_iso2: string;
  admin1?: string | null;
}
export interface Fact {
  id: string;
  domain: "soil" | "water" | "climate" | "vegetation" | "terrain" | "farm" | "rotation";
  label_key: string;
  quantity?: Quantity | null;
  category?: string | null;
  depth_cm?: [number, number] | null;
  provenance: Provenance;
}
export interface Quantity {
  value: number;
  unit:
    | "1"
    | "%"
    | "[pH]"
    | "mm"
    | "mm/d"
    | "m3/m3"
    | "Cel"
    | "Cel.d"
    | "d"
    | "m"
    | "cm"
    | "ha"
    | "deg"
    | "kg/ha"
    | "t/ha"
    | "g/kg"
    | "cmol/kg"
    | "MJ/m2/d"
    | "m/s";
  uncertainty?: Uncertainty | null;
}
export interface Uncertainty {
  kind: "sd" | "ci95" | "range" | "qualitative";
  low?: number | null;
  high?: number | null;
  sd?: number | null;
  note?: string | null;
}
export interface Provenance {
  kind:
    | "satellite_product"
    | "ml_estimate"
    | "deterministic_calc"
    | "reference"
    | "farmer_input"
    | "llm_explanation"
    | "weather_data";
  data_status: "live" | "cached" | "fixture" | "unavailable";
  source?: Source | null;
  method?: string | null;
  model?: ModelInfo | null;
  inputs?: string[];
  spatial?: Spatial | null;
  temporal?: Temporal | null;
  retrieved_at: string;
}
export interface Source {
  provider: string;
  product_id: string;
  version: string;
  level?: string | null;
  doi?: string | null;
  url?: string | null;
}
export interface ModelInfo {
  name: string;
  version: string;
  validation?: Validation | null;
}
export interface Validation {
  metric: string;
  value: number;
  n: number;
  note?: string | null;
}
export interface Spatial {
  resolution_m: number;
  note?: string | null;
}
export interface Temporal {
  start: string;
  end: string;
  aggregation?: string | null;
}
export interface TimeseriesInfo {
  metric_id: string;
  label_key: string;
  unit:
    | "1"
    | "%"
    | "[pH]"
    | "mm"
    | "mm/d"
    | "m3/m3"
    | "Cel"
    | "Cel.d"
    | "d"
    | "m"
    | "cm"
    | "ha"
    | "deg"
    | "kg/ha"
    | "t/ha"
    | "g/kg"
    | "cmol/kg"
    | "MJ/m2/d"
    | "m/s";
  range: TimeRange;
  provenance: Provenance;
}
export interface TimeRange {
  start: string;
  end: string;
}
export interface MapLayer {
  layer_id: string;
  label_key: string;
  kind: "raster" | "vector" | "3d";
  provenance: Provenance;
}
export interface ChartInfo {
  chart_id: string;
  /**
   * @minItems 1
   */
  metric_ids: string[];
}
export interface CropSeason {
  season: string;
  crop_id: string;
  source: "farmer_input" | "inferred";
}
export interface IrrigationConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "irrigation_available";
  value: boolean;
}
export interface FertilizerBudgetConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "fertilizer_budget";
  value: "minimize_n" | "low" | "normal";
}
export interface ErosionProtectionConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "erosion_protection";
  value: boolean;
}
export interface RotationLengthConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "rotation_length_years";
  value: number;
}
export interface ExcludedCropsConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "excluded_crops";
  /**
   * @minItems 1
   */
  value: string[];
}
export interface PreferredCropsConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "preferred_crops";
  /**
   * @minItems 1
   */
  value: string[];
}
export interface PreviousCropConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "previous_crop";
  value: string;
}
export interface OrganicOnlyConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "organic_only";
  value: boolean;
}
export interface MachineryLimitConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "machinery_limit";
  value: string;
}
export interface MarketPreferenceConstraint {
  id: string;
  status: "proposed" | "confirmed" | "rejected";
  source: "farmer_text" | "farmer_voice" | "farmer_ui" | "agent_inferred";
  evidence?: string | null;
  confidence?: number | null;
  type: "market_preference";
  /**
   * @minItems 1
   */
  value: string[];
}
export interface UIState {
  active_layer_ids?: string[];
  time_range?: TimeRange | null;
  chart_focus?: ChartFocus | null;
  selected_rotation_ids?: string[];
  scenario?: "typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year";
  screen_id?: string | null;
}
export interface ChartFocus {
  chart_id: string;
  /**
   * @minItems 1
   */
  metric_ids: string[];
  highlight?: TimeRange | null;
}
/**
 * What the geo service (member 1) returns for a field; the host merges it into FieldContext.
 */
export interface FieldProfile {
  field_id: string;
  facts: Fact[];
  timeseries_index?: TimeseriesInfo[];
  available_layers?: MapLayer[];
}
export interface AgentRequest {
  request_id: string;
  session_id: string;
  locale: "uk" | "en" | "auto";
  message: UserMessage;
  /**
   * @maxItems 10
   */
  history?: HistoryTurn[];
  field_context: FieldContext;
  client: ClientInfo;
}
export interface UserMessage {
  text?: string | null;
  /**
   * @maxItems 4
   */
  attachments?: Attachment[];
}
export interface Attachment {
  attachment_id: string;
  kind: "image" | "audio";
}
export interface HistoryTurn {
  role: "user" | "assistant";
  text: string;
}
export interface ClientInfo {
  widget_version: string;
  capabilities?: ClientCapabilities | null;
}
/**
 * What the host app can show or do right now (HostCapabilities of the widget). Unknown when absent.
 */
export interface ClientCapabilities {
  /**
   * @maxItems 20
   */
  actions?: (
    | "map.set_layers"
    | "map.focus_field"
    | "time.set_range"
    | "chart.focus"
    | "compare.set_constraints"
    | "compare.select_rotations"
    | "scenario.set"
    | "panel.open"
    | "app.open_screen"
  )[];
  /**
   * @maxItems 30
   */
  screens?: AppScreen[];
}
/**
 * A screen of the host app the assistant may open or point to. Host-declared UI data, not permission.
 */
export interface AppScreen {
  screen_id: string;
  label: string;
  how_to_open?: string | null;
}
export interface TurnStartedEvent {
  event: "turn.started";
  turn_id: string;
}
export interface TextDeltaEvent {
  event: "text.delta";
  text: string;
}
export interface ToolStartedEvent {
  event: "tool.started";
  call_id: string;
  tool:
    | "get_field_facts"
    | "get_timeseries"
    | "compare_rotations"
    | "search_knowledge"
    | "propose_constraints"
    | "propose_ui_actions"
    | "present_blocks"
    | "get_weather_forecast"
    | "get_season_weather"
    | "check_sowing_soil"
    | "analyze_soil_photo";
}
export interface ToolFinishedEvent {
  event: "tool.finished";
  call_id: string;
  status: "ok" | "error" | "timeout";
  latency_ms: number;
}
export interface TurnCompletedEvent {
  event: "turn.completed";
  message: AgentMessage;
}
export interface AgentMessage {
  turn_id: string;
  locale: "uk" | "en";
  /**
   * @minItems 1
   */
  blocks: MessageBlock[];
  facts?: Fact[];
  citations?: Citation[];
  constraints_proposed?: Constraint[];
  ui_actions?: UIActionEnvelope[];
  warnings?: AgentWarning[];
  meta: ResponseMeta;
}
export interface TextBlock {
  type: "text";
  markdown: string;
}
export interface ClarifyingQuestionBlock {
  type: "clarifying_question";
  text: string;
  /**
   * @maxItems 6
   */
  options?: string[];
}
/**
 * A row of value cards: several values of this field at a glance.
 */
export interface MetricsBlock {
  type: "metrics";
  title?: string | null;
  /**
   * @minItems 1
   * @maxItems 6
   */
  fact_ids: string[];
  scenario?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year") | null;
}
/**
 * Options side by side (rows) on the same measures (columns).
 */
export interface TableBlock {
  type: "table";
  title?: string | null;
  /**
   * @minItems 1
   * @maxItems 5
   */
  columns: string[];
  /**
   * @minItems 1
   * @maxItems 10
   */
  rows: TableRow[];
  scenario?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year") | null;
}
export interface TableRow {
  label: string;
  /**
   * @minItems 1
   * @maxItems 5
   */
  fact_ids: (string | null)[];
}
/**
 * One measure compared across options; all bars share one unit.
 */
export interface BarChartBlock {
  type: "bar_chart";
  title: string;
  /**
   * @minItems 2
   * @maxItems 8
   */
  bars: ChartBar[];
  scenario?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year") | null;
}
export interface ChartBar {
  label: string;
  fact_id: string;
}
/**
 * A short notice set apart from the text (a caution, or something to know).
 */
export interface CalloutBlock {
  type: "callout";
  tone: "info" | "warning";
  text: string;
  /**
   * @maxItems 3
   */
  fact_ids?: string[];
  scenario?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year") | null;
}
export interface Citation {
  id: string;
  chunk_id: string;
  doc_id: string;
  title: string;
  publisher: string;
  year: number;
  edition?: string | null;
  locator: Locator;
  url: string;
  quote: string;
  language: "uk" | "en";
  licence: string;
}
export interface Locator {
  chapter?: string | null;
  section?: string | null;
  table?: string | null;
  page?: number | null;
}
export interface UIActionEnvelope {
  id: string;
  field_context_revision: number;
  trigger: "auto" | "chip";
  label: string;
  reason?: string | null;
  action: UIAction;
}
export interface SetLayersAction {
  type: "map.set_layers";
  /**
   * @minItems 1
   */
  layer_ids: string[];
  mode: "replace" | "add";
}
export interface FocusFieldAction {
  type: "map.focus_field";
}
export interface SetTimeRangeAction {
  start: string;
  end: string;
  type: "time.set_range";
}
export interface FocusChartAction {
  chart_id: string;
  /**
   * @minItems 1
   */
  metric_ids: string[];
  highlight?: TimeRange | null;
  type: "chart.focus";
}
export interface SetConstraintsAction {
  type: "compare.set_constraints";
  constraints: Constraint[];
}
export interface SelectRotationsAction {
  type: "compare.select_rotations";
  /**
   * @minItems 1
   */
  rotation_ids: string[];
}
export interface SetScenarioAction {
  type: "scenario.set";
  scenario: "typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year";
}
export interface OpenPanelAction {
  type: "panel.open";
  panel: "soil_portrait" | "comparison" | "calendar" | "citations";
  ref_id?: string | null;
  scenario?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year") | null;
}
/**
 * Show one of the app's own screens (AppScreen.screen_id from the host's capabilities). View only.
 */
export interface OpenScreenAction {
  type: "app.open_screen";
  screen_id: string;
}
export interface AgentWarning {
  code: "ungrounded_number" | "source_unavailable" | "fixture_data" | "low_confidence";
  detail?: string | null;
}
export interface ResponseMeta {
  provider: string;
  model_id: string;
  prompt_version: string;
  latency_ms: number;
}
export interface ErrorEvent {
  code:
    | "invalid_request"
    | "rate_limited"
    | "upstream_timeout"
    | "upstream_unavailable"
    | "llm_error"
    | "payload_too_large"
    | "internal";
  message_key: string;
  retryable: boolean;
  request_id?: string | null;
  detail?: string | null;
  event: "error";
}
/**
 * Body of every non-2xx HTTP response from the agent service.
 */
export interface ErrorEnvelope {
  error: ErrorBody;
}
export interface ErrorBody {
  code:
    | "invalid_request"
    | "rate_limited"
    | "upstream_timeout"
    | "upstream_unavailable"
    | "llm_error"
    | "payload_too_large"
    | "internal";
  message_key: string;
  retryable: boolean;
  request_id?: string | null;
  detail?: string | null;
}
/**
 * Agent -> geo service (member 1).
 */
export interface TimeseriesQuery {
  field_id: string;
  metric_id: string;
  range: TimeRange;
  aggregation: "daily" | "dekad" | "monthly";
}
export interface Timeseries {
  metric_id: string;
  unit:
    | "1"
    | "%"
    | "[pH]"
    | "mm"
    | "mm/d"
    | "m3/m3"
    | "Cel"
    | "Cel.d"
    | "d"
    | "m"
    | "cm"
    | "ha"
    | "deg"
    | "kg/ha"
    | "t/ha"
    | "g/kg"
    | "cmol/kg"
    | "MJ/m2/d"
    | "m/s";
  aggregation: "daily" | "dekad" | "monthly";
  points: [string, number | null][];
  provenance: Provenance;
}
/**
 * Agent or host -> simulation service (member 2).
 */
export interface CompareRotationsRequest {
  field_id: string;
  field_context_revision: number;
  constraints?: Constraint[];
  scenario: "typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year";
  rotation_ids?: string[];
}
export interface RotationComparison {
  scenario: "typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year";
  /**
   * @minItems 1
   */
  rotations: RotationOption[];
  facts?: Fact[];
  constraint_weights_applied?: {
    [k: string]: number;
  };
}
export interface RotationOption {
  rotation_id: string;
  /**
   * @minItems 1
   */
  sequence: RotationStep[];
  scores: RotationScores;
  rank: number;
  reason_fact_ids?: string[];
}
export interface RotationStep {
  year_index: number;
  season: "winter" | "spring" | "summer" | "cover";
  crop_id: string;
}
/**
 * A service sends only what it computes: an absent score is "not computed", never zero.
 */
export interface RotationScores {
  water_risk: Score;
  soil_cover_days?: Score | null;
  n_input?: Score | null;
  yield_risk?: Score | null;
  yield_loss_water?: Score | null;
}
export interface Score {
  value: number;
  unit:
    | "1"
    | "%"
    | "[pH]"
    | "mm"
    | "mm/d"
    | "m3/m3"
    | "Cel"
    | "Cel.d"
    | "d"
    | "m"
    | "cm"
    | "ha"
    | "deg"
    | "kg/ha"
    | "t/ha"
    | "g/kg"
    | "cmol/kg"
    | "MJ/m2/d"
    | "m/s";
  uncertainty?: Uncertainty | null;
  provenance: Provenance;
}
