/* Generated from planning-v1.schema.json; run planning_v1.export. Do not edit. */

/**
 * Export-only index of public DTOs, never an HTTP request body.
 */
export interface PlanningContracts {
  schema_version?: "1.0.0";
  field_profile: FieldProfileSnapshot;
  weather: WeatherScenarioSet;
  suitability: SuitabilityAssessment;
  candidate: CandidateSequence;
  evaluation: SequenceEvaluation;
  request: FarmPlanningRequest;
  run: PlanningRun;
  alternative: PlanAlternative;
  comparison: FarmPlanComparison;
  model_card: ModelCard;
  observation: TrainingObservation;
  crop_registry: CropRegistry;
  profile_write: ProfileWriteRequest;
  apply_request: ApplyPlanRequest;
  apply_result: ApplyPlanResult;
  replan: ReplanRequest;
  error: PlanningError;
}
export interface FieldProfileSnapshot {
  schema_version?: "1.0.0";
  snapshot_id: string;
  farm_id: string;
  field_id: string;
  profile_revision: number;
  geometry_revision: number;
  as_of: string;
  geometry: FieldGeometry;
  area_ha: number;
  land_status: "arable" | "non_arable" | "ocean" | "ice" | "unknown";
  /**
   * @maxItems 100
   */
  history: CropHistoryEntry[];
  history_complete_since: number | null;
  current_crop: CropRef | null;
  /**
   * @maxItems 30
   */
  soil: SoilHorizon[];
  water_state: WaterState | null;
  management: Management;
  terrain: TerrainRadiation | null;
  /**
   * @maxItems 50
   */
  hazards: HazardEvidence[];
  /**
   * @maxItems 200
   */
  sources: SourceRecord[];
  /**
   * @maxItems 50
   */
  missing_data: Reason[];
}
export interface FieldGeometry {
  type: "Polygon";
  crs: "EPSG:4326";
  /**
   * @minItems 1
   * @maxItems 50
   */
  coordinates: [number, number][][];
}
export interface CropHistoryEntry {
  season: number;
  crop: CropRef | null;
  status: "confirmed" | "reported" | "unknown";
  sowing_date: string | null;
  harvest_date: string | null;
  /**
   * @maxItems 20
   */
  source_ids: string[];
}
export interface CropRef {
  crop_id: string;
  cultivar_id: string | null;
  profile_version: string | null;
}
export interface SoilHorizon {
  top_cm: number;
  bottom_cm: number;
  sand_fraction: number | null;
  silt_fraction: number | null;
  clay_fraction: number | null;
  field_capacity_m3_m3: number | null;
  wilting_point_m3_m3: number | null;
  saturation_m3_m3: number | null;
  organic_carbon_g_kg: number | null;
  ph: number | null;
  salinity_ds_m: number | null;
  /**
   * @minItems 1
   * @maxItems 20
   */
  source_ids: string[];
  quality: "valid" | "partial" | "missing" | "rejected";
}
export interface WaterState {
  observed_at: string;
  available_at: string;
  depth_top_cm: number;
  depth_bottom_cm: number;
  volumetric_m3_m3: number | null;
  available_reserve_mm: number | null;
  available_capacity_mm: number | null;
  /**
   * @minItems 1
   * @maxItems 20
   */
  source_ids: string[];
}
export interface Management {
  production_system: "outdoor";
  /**
   * @maxItems 10
   */
  irrigation: IrrigationLimit[];
  /**
   * @maxItems 100
   */
  equipment_ids: string[];
  residue_cover_fraction: number | null;
  tillage: "conventional" | "reduced" | "no_till" | "unknown";
}
export interface IrrigationLimit {
  season: number;
  max_mm: number | null;
  system: "none" | "drip" | "sprinkler" | "surface" | "managed_paddy" | "unknown";
  /**
   * @maxItems 20
   */
  source_ids: string[];
}
export interface TerrainRadiation {
  latitude_deg: number;
  longitude_deg: number;
  elevation_m: number | null;
  slope_deg: number | null;
  aspect_deg: number | null;
  aspect_convention: "clockwise_from_true_north";
  solar_time: string | null;
  solar_zenith_deg: number | null;
  surface_shortwave_w_m2: number | null;
  /**
   * @maxItems 20
   */
  source_ids: string[];
}
export interface HazardEvidence {
  id: string;
  kind: "wind" | "frost" | "heat" | "heavy_rain" | "river_discharge" | "inundation";
  status: "available" | "insufficient_data" | "unsupported" | "error";
  indicator: "threshold" | "efi" | "river_warning" | "hydraulic_depth";
  value: number | null;
  unit: "m/s" | "degC" | "mm/day" | "1" | "m3/s" | "m";
  valid_window: DateWindow;
  /**
   * @maxItems 20
   */
  source_ids: string[];
  /**
   * @maxItems 50
   */
  reasons: Reason[];
}
export interface DateWindow {
  start: string;
  end: string;
}
export interface Reason {
  code: string;
  message: string;
  /**
   * @minItems 1
   * @maxItems 50
   */
  evidence: EvidenceRef[];
}
export interface EvidenceRef {
  id: string;
  kind: "source" | "rule" | "model" | "constraint" | "assumption" | "result";
  version: string;
  locator: string;
}
export interface SourceRecord {
  id: string;
  provider: string;
  product: string;
  version: string;
  kind: "measured" | "modelled" | "user_entered" | "synthetic" | "reference";
  acquired_at: string;
  available_at: string;
  retrieved_at: string;
  checksum_sha256: string;
  licence: string;
  attribution: string;
  native_resolution_m: number | null;
  coverage_fraction: number | null;
  spatial_method: "sensor" | "representative_point" | "area_weighted" | "polygon" | "none";
  quality: "valid" | "partial" | "missing" | "rejected";
}
export interface WeatherScenarioSet {
  schema_version?: "1.0.0";
  scenario_set_id: string;
  as_of: string;
  issued_at: string;
  cycle_at: string;
  model_version: string;
  valid_window: DateWindow;
  scenario_type: "forecast" | "seasonal" | "historical_replay" | "synthetic";
  stale_after: string;
  weight_calibration: string | null;
  /**
   * @minItems 1
   * @maxItems 200
   */
  sources: SourceRecord[];
  /**
   * @minItems 1
   * @maxItems 64
   */
  scenarios: WeatherScenario[];
}
export interface WeatherScenario {
  trajectory_id: string;
  member_id: string;
  weight: number | null;
  /**
   * @minItems 1
   * @maxItems 30
   */
  traces: WeatherTrace[];
}
export interface WeatherTrace {
  field_snapshot_id: string;
  grid_id: string;
  source_id: string;
  native_resolution_m: number;
  representative_latitude: number;
  representative_longitude: number;
  /**
   * @minItems 1
   * @maxItems 2192
   */
  days: DailyWeather[];
}
export interface DailyWeather {
  date: string;
  source_date: string | null;
  lead_days: number | null;
  tmin_c: number | null;
  tmax_c: number | null;
  liquid_rain_mm: number | null;
  et0_mm: number | null;
  shortwave_mj_m2: number | null;
  wind_m_s: number | null;
  relative_humidity_fraction: number | null;
  snow_water_equivalent_mm: number | null;
  soil_frozen: boolean | null;
  quality: "valid" | "partial" | "missing" | "rejected";
}
export interface SuitabilityAssessment {
  schema_version?: "1.0.0";
  assessment_id: string;
  field_snapshot_id: string;
  season: number;
  crop: CropRef;
  status: "eligible" | "ineligible" | "insufficient_data" | "out_of_domain" | "unsupported" | "error";
  rulebook_version: string;
  /**
   * @maxItems 20
   */
  required_capabilities: (
    | "phenology"
    | "yield"
    | "root_zone_water"
    | "surface_moisture"
    | "irrigation"
    | "state_carry"
    | "winter"
    | "snow"
    | "frozen_soil"
    | "paddy"
    | "salinity"
    | "fertility"
    | "waterlogging"
    | "inundation"
    | "solar_geometry"
  )[];
  /**
   * @maxItems 200
   */
  checked_rule_ids: string[];
  /**
   * @maxItems 200
   */
  unchecked_rule_ids: string[];
  /**
   * @minItems 1
   * @maxItems 100
   */
  reasons: Reason[];
}
export interface CandidateSequence {
  schema_version?: "1.0.0";
  sequence_id: string;
  field_snapshot_id: string;
  field_id: string;
  rulebook_version: string;
  /**
   * @minItems 1
   * @maxItems 3
   */
  assignments: CropAssignment[];
  /**
   * @minItems 1
   * @maxItems 3
   */
  assessments: SuitabilityAssessment[];
}
export interface CropAssignment {
  field_id: string;
  season: number;
  crop: CropRef;
  sowing_window: DateWindow | null;
  harvest_window: DateWindow | null;
  locked: boolean;
  commitment_id: string | null;
}
export interface SequenceEvaluation {
  schema_version?: "1.0.0";
  evaluation_id: string;
  sequence_id: string;
  field_snapshot_id: string;
  scenario_set_id: string;
  as_of: string;
  model: ModelRef;
  applicability: "validated" | "baseline" | "experimental" | "out_of_domain" | "unsupported";
  uncertainty_scope: string;
  /**
   * @minItems 1
   * @maxItems 192
   */
  seasons: SeasonEvaluation[];
  input_sha256: string;
  /**
   * @maxItems 100
   */
  reasons: Reason[];
}
export interface ModelRef {
  model_id: string;
  version: string;
  artifact_sha256: string;
  parameter_sha256: string;
}
export interface SeasonEvaluation {
  season: number;
  crop: CropRef;
  trajectory_id: string;
  status: "available" | "insufficient_data" | "out_of_domain" | "unsupported" | "not_applicable" | "error";
  yield_t_ha: Metric;
  harvest_basis: HarvestBasis | null;
  root_zone_reserve_mm: Metric;
  root_depth_cm: number | null;
  surface_moisture_m3_m3: Metric;
  surface_depth_top_cm: number | null;
  surface_depth_bottom_cm: number | null;
  stress_days: Metric;
  irrigation_mm: Metric;
  opening_state_id: string | null;
  closing_state_id: string | null;
  /**
   * @maxItems 100
   */
  reasons: Reason[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema definition
 * via the `patternProperty` "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$".
 */
export interface Metric {
  status: "available" | "insufficient_data" | "out_of_domain" | "unsupported" | "not_applicable" | "error";
  value: number | null;
  lower: number | null;
  upper: number | null;
  unit: "t/ha" | "mm" | "m3/m3" | "days" | "currency/ha" | "currency" | "ha" | "1";
  uncertainty: Uncertainty;
  /**
   * @maxItems 50
   */
  reasons: Reason[];
}
export interface Uncertainty {
  kind: "none" | "scenario_range" | "predictive_interval" | "measurement_interval";
  coverage: number | null;
  calibration_scope: string | null;
  method_version: string;
}
export interface HarvestBasis {
  basis: "dry_matter" | "standard_moisture" | "fresh_weight";
  moisture_fraction: number;
}
export interface FarmPlanningRequest {
  schema_version?: "1.0.0";
  request_id: string;
  farm_id: string;
  as_of: string;
  start_season: number;
  season_count: 1 | 3;
  /**
   * @minItems 1
   * @maxItems 30
   */
  fields: FieldProfileSnapshot[];
  weather: WeatherScenarioSet;
  /**
   * @minItems 1
   * @maxItems 30
   */
  enabled_crops: CropRef[];
  rulebook_version: string;
  goal: "reference_margin" | "worst_case_margin" | "water_demand";
  reference_trajectory_id: string | null;
  /**
   * @maxItems 90
   */
  locks: CropAssignment[];
  /**
   * @maxItems 3
   */
  resources: ResourceLimit[];
  /**
   * @maxItems 90
   */
  crop_areas: CropAreaLimit[];
  /**
   * @maxItems 100
   */
  equipment: EquipmentLimit[];
  /**
   * @maxItems 5760
   */
  economics: EconomicAssumption[];
  economic_correlation_note: string;
  solver_limits: SolverLimits;
}
export interface ResourceLimit {
  constraint_id: string;
  season: number;
  budget: number | null;
  currency: string | null;
  irrigation_m3: number | null;
}
export interface CropAreaLimit {
  constraint_id: string;
  season: number;
  crop_id: string;
  min_ha: number;
  max_ha: number | null;
  max_share: number | null;
}
export interface EquipmentLimit {
  constraint_id: string;
  equipment_id: string;
  window: DateWindow;
  capacity_ha: number;
}
export interface EconomicAssumption {
  assumption_id: string;
  crop: CropRef;
  season: number;
  trajectory_id: string;
  price_per_t: number | null;
  variable_cost_per_ha: number | null;
  irrigation_cost_per_mm_ha: number | null;
  currency: string | null;
  price_date: string | null;
  harvest_basis: HarvestBasis | null;
  source: string;
  available_at: string;
  user_yield_t_ha: number | null;
  yield_origin: "model" | "user_assumption";
}
export interface SolverLimits {
  max_nodes: number;
  max_seconds: number;
  max_candidates_per_field: number;
  max_alternatives: number;
}
export interface PlanningRun {
  schema_version?: "1.0.0";
  run_id: string;
  request: FarmPlanningRequest;
  input_sha256: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  attempt: number;
  created_at: string;
  updated_at: string;
  comparison: FarmPlanComparison | null;
  error: Reason | null;
}
export interface FarmPlanComparison {
  schema_version?: "1.0.0";
  comparison_id: string;
  request_id: string;
  farm_id: string;
  solver_status: "not_run" | "optimal" | "feasible" | "infeasible" | "limit_reached" | "error";
  explored_nodes: number;
  elapsed_seconds: number;
  /**
   * @maxItems 3
   */
  alternatives: PlanAlternative[];
  /**
   * @minItems 1
   * @maxItems 30
   */
  snapshots: SnapshotRef[];
  scenario_set_id: string;
  rulebook_version: string;
  input_sha256: string;
  /**
   * @maxItems 100
   */
  diagnostics: Reason[];
}
export interface PlanAlternative {
  schema_version?: "1.0.0";
  alternative_id: string;
  label: string;
  /**
   * @minItems 1
   * @maxItems 90
   */
  assignments: CropAssignment[];
  /**
   * @minItems 1
   * @maxItems 30
   */
  sequence_ids: string[];
  /**
   * @maxItems 30
   */
  evaluation_ids: string[];
  /**
   * @maxItems 192
   */
  scenario_results: ScenarioEconomics[];
  objective_value: number | null;
  objective_unit: "currency" | "mm_ha" | "stress_days_ha";
  /**
   * @minItems 1
   * @maxItems 100
   */
  reasons: Reason[];
}
export interface ScenarioEconomics {
  trajectory_id: string;
  season: number;
  currency: string | null;
  gross_margin: Metric;
  irrigation_m3: number | null;
  variable_cost: number | null;
  /**
   * @maxItems 300
   */
  assumption_ids: string[];
}
export interface SnapshotRef {
  field_id: string;
  snapshot_id: string;
  profile_revision: number;
  geometry_revision: number;
}
export interface ModelCard {
  schema_version?: "1.0.0";
  model: ModelRef;
  licence_identifier: string;
  licence_source: string;
  licence_sha256: string;
  commercial_use: "permitted" | "restricted" | "unverified";
  /**
   * @maxItems 100
   */
  data_permissions: string[];
  /**
   * @minItems 1
   * @maxItems 20
   */
  capabilities: (
    | "phenology"
    | "yield"
    | "root_zone_water"
    | "surface_moisture"
    | "irrigation"
    | "state_carry"
    | "winter"
    | "snow"
    | "frozen_soil"
    | "paddy"
    | "salinity"
    | "fertility"
    | "waterlogging"
    | "inundation"
    | "solar_geometry"
  )[];
  /**
   * @minItems 1
   * @maxItems 100
   */
  supported_crops: CropRef[];
  /**
   * @maxItems 100
   */
  supported_region_ids: string[];
  validated_scope: string | null;
  training_status: "not_trained" | "insufficient_training_data" | "trained" | "failed";
  /**
   * @maxItems 100
   */
  cohorts: ValidationCohort[];
  promotion: "baseline_only" | "candidate" | "passed" | "failed" | "insufficient_evidence";
  gate_protocol_version: string;
  gate_frozen_at: string;
  holdout_opened_at: string | null;
  fallback_model: ModelRef | null;
  /**
   * @minItems 1
   * @maxItems 100
   */
  reasons: Reason[];
}
export interface ValidationCohort {
  partition: "fit" | "calibration" | "test";
  /**
   * @maxItems 100
   */
  region_ids: string[];
  /**
   * @maxItems 10000
   */
  farm_group_ids: string[];
  /**
   * @maxItems 10000
   */
  grid_group_ids: string[];
  /**
   * @maxItems 100
   */
  crops: string[];
  /**
   * @maxItems 200
   */
  years: number[];
  observation_count: number;
  metrics: {
    [k: string]: Metric;
  };
}
export interface TrainingObservation {
  schema_version?: "1.0.0";
  observation_id: string;
  farm_group_id: string;
  field_id: string;
  geographic_group_id: string;
  grid_group_id: string;
  geometry_revision: number;
  region_id: string;
  crop: CropRef | null;
  observed_at: string;
  available_at: string;
  kind: "soil_moisture" | "yield" | "phenology" | "canopy_cover" | "biomass" | "irrigation";
  value: number | null;
  unit: "m3/m3" | "t/ha" | "stage" | "1" | "kg/ha" | "mm";
  depth_top_cm: number | null;
  depth_bottom_cm: number | null;
  crop_stage: string | null;
  sowing_date: string | null;
  harvest_date: string | null;
  irrigation_mm: number | null;
  harvest_basis: HarvestBasis | null;
  field_snapshot_id: string | null;
  source_checksum_sha256: string;
  source_locator: string;
  permission: string;
  quality: "valid" | "partial" | "missing" | "rejected";
  synthetic: boolean;
}
export interface CropRegistry {
  schema_version?: "1.0.0";
  registry_version: string;
  /**
   * @minItems 1
   * @maxItems 100
   */
  entries: CropRegistryEntry[];
}
export interface CropRegistryEntry {
  crop_id: "Wheat" | "Maize" | "Soy" | "Barley" | "Sunflower" | "Rapeseed" | "Peas" | "Rice";
  family: string;
  /**
   * @maxItems 20
   */
  required_capabilities: (
    | "phenology"
    | "yield"
    | "root_zone_water"
    | "surface_moisture"
    | "irrigation"
    | "state_carry"
    | "winter"
    | "snow"
    | "frozen_soil"
    | "paddy"
    | "salinity"
    | "fertility"
    | "waterlogging"
    | "inundation"
    | "solar_geometry"
  )[];
  note: string;
}
export interface ProfileWriteRequest {
  schema_version?: "1.0.0";
  request_id: string;
  expected_revision: number | null;
  profile: FieldProfileSnapshot;
}
export interface ApplyPlanRequest {
  schema_version?: "1.0.0";
  request_id: string;
  farm_id: string;
  run_id: string;
  alternative_id: string;
  expected_input_sha256: string;
  /**
   * @minItems 1
   * @maxItems 30
   */
  expected_snapshots: SnapshotRef[];
  confirmed_by_human: true;
}
export interface ApplyPlanResult {
  schema_version?: "1.0.0";
  request_id: string;
  status: "applied" | "already_applied" | "stale" | "forbidden" | "infeasible" | "error";
  applied_at: string | null;
  /**
   * @maxItems 100
   */
  reasons: Reason[];
}
export interface ReplanRequest {
  schema_version?: "1.0.0";
  previous_run_id: string;
  request: FarmPlanningRequest;
  /**
   * @maxItems 90
   */
  preserve_commitment_ids: string[];
}
export interface PlanningError {
  schema_version?: "1.0.0";
  request_id: string | null;
  code: "invalid_input" | "not_found" | "forbidden" | "stale" | "conflict" | "unavailable" | "internal";
  retryable: boolean;
  /**
   * @minItems 1
   * @maxItems 100
   */
  reasons: Reason[];
}
