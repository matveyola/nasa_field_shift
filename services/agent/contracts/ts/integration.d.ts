/* Generated from contracts/schema/fieldshift-integration.schema.json (PROPOSED v0.1). Do not edit; run: npm run gen:ts:integration */

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

/**
 * Index of the service integration envelopes (PROPOSED v0.1). Export root only.
 */
export interface FieldShiftIntegrationContracts {
  capabilities_response: CapabilitiesResponse;
  field_profile_request: FieldProfileRequest;
  field_profile_response: FieldProfileResponse;
  comparison_request: ComparisonRequest;
  comparison_response: ComparisonResponse;
  integration_error: IntegrationError;
}
/**
 * GET /v1/capabilities on either service. A capability is an offer, never a completed computation.
 */
export interface CapabilitiesResponse {
  service: ServiceInfo;
  geo?: GeoCapabilities | null;
  simulation?: SimulationCapabilities | null;
}
export interface ServiceInfo {
  service: "geo" | "simulation";
  implementation: string;
  mode: "live" | "stub" | "fixture";
  contract_version: "0.1";
}
export interface GeoCapabilities {
  field_profile: boolean;
  timeseries: boolean;
  datasets?: DatasetCapability[];
}
export interface DatasetCapability {
  dataset_id: string;
  kind: "satellite_product" | "ml_estimate" | "deterministic_calc" | "reference";
  available: boolean;
  note?: string | null;
}
export interface SimulationCapabilities {
  compare_rotations: boolean;
  scenarios?: ("typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year")[];
  max_rotations?: number;
  result_ttl_s?: number;
  async_jobs?: boolean;
}
/**
 * POST /v1/field-profile (geo service).
 */
export interface FieldProfileRequest {
  contract_version: "0.1";
  request_id: string;
  field_id: string;
}
export interface FieldProfileResponse {
  contract_version: "0.1";
  request_id: string;
  service: ServiceInfo;
  status: "complete" | "partial";
  generated_at: string;
  geometry: Point | Polygon;
  /**
   * @minItems 2
   * @maxItems 2
   */
  centroid: [number, number];
  profile: FieldProfile;
  warnings?: IntegrationWarning[];
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
/**
 * What the geo service (member 1) returns for a field; the host merges it into FieldContext.
 */
export interface FieldProfile {
  field_id: string;
  facts: Fact[];
  timeseries_index?: TimeseriesInfo[];
  available_layers?: MapLayer[];
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
export interface IntegrationWarning {
  code:
    | "partial_data"
    | "dataset_unavailable"
    | "unverified_version"
    | "coarse_resolution"
    | "model_not_validated"
    | "fixture_data"
    | "other";
  detail?: string | null;
}
/**
 * POST /v1/comparisons (simulation service). Send `Idempotency-Key: <idempotency_key>` too.
 */
export interface ComparisonRequest {
  contract_version: "0.1";
  request_id: string;
  idempotency_key: string;
  comparison: CompareRotationsRequest;
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
/**
 * Answer to POST /v1/comparisons and GET /v1/comparisons/{result_id}.
 */
export interface ComparisonResponse {
  contract_version: "0.1";
  request_id: string;
  service: ServiceInfo;
  result_id: string;
  status: "accepted" | "running" | "completed" | "failed";
  field_id: string;
  field_context_revision: number;
  scenario: "typical" | "drought_2024" | "heat_plus_2_5c" | "driest_recent_year" | "hottest_recent_year";
  engine: EngineInfo;
  created_at: string;
  computed_at?: string | null;
  expires_at: string;
  comparison?: RotationComparison | null;
  failure?: ComparisonFailure | null;
  warnings?: IntegrationWarning[];
}
export interface EngineInfo {
  name: string;
  version: string;
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
export interface ComparisonFailure {
  code: "insufficient_data" | "unsupported_scenario" | "engine_error" | "timeout";
  message_key: string;
}
/**
 * Body of every non-2xx response from the geo or simulation service.
 */
export interface IntegrationError {
  error: IntegrationErrorBody;
}
export interface IntegrationErrorBody {
  code:
    | "invalid_request"
    | "not_found"
    | "field_not_authorized"
    | "unsupported_scenario"
    | "unsupported_contract"
    | "expired"
    | "unavailable"
    | "internal";
  message_key: string;
  retryable: boolean;
  request_id?: string | null;
}
