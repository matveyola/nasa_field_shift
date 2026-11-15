export type Scenario = 'baseline' | 'dry_year' | 'less_rain'
export const scenarios: { id: Scenario; label: string }[] = [
  { id: 'baseline', label: 'Baseline' },
  { id: 'dry_year', label: 'Dry year' },
  { id: 'less_rain', label: 'Less rain' },
]

export function scenarioOrDefault(value?: string): Scenario {
  return scenarios.find(({ id }) => id === value)?.id ?? 'dry_year'
}

export type Position = [number, number]

export type SatelliteImage = {
  id: string
  acquired_at: string
  on_or_before: string
  tile_cloud_percent: number
  visual_url: string
  catalog_url: string
  epsg: number
  bounds: [west: number, south: number, east: number, north: number]
}
export type SatelliteScene = SatelliteImage & { field_id: string }
export type Polygon = { type: 'Polygon'; coordinates: Position[][] }
export type FieldPlanting = { season: number; geometry: Polygon; area_ha: number }
export type Field = {
  id: string
  name: string
  crop: string
  area_ha: number
  geometry: Polygon
  boundary_revision: number
  planting: FieldPlanting | null
  irrigation_available?: boolean | null
  boundary_origin?: 'example' | 'registered'
}
export type NewField = Pick<Field, 'name' | 'crop' | 'area_ha' | 'geometry'> & { season?: number }
export type MoistureSource = 'ml' | 'historical' | 'demo'
type MoistureProvenance = {
  name: string
  provider: string
  url: string
  resolution_degrees: number
  grid_latitude: number
  grid_longitude: number
  sample_count: number
  requested_latitude: number
  requested_longitude: number
} & ({ kind: 'reanalysis' } | {
  kind: 'ml'
  model_version: string
  generated_at: string
  experimental?: boolean
  soil_profile?: { map_unit: string | null; component_name: string | null; clay: number | null; sand: number | null } | null
  soil_source_url?: string | null
  interval?: { lower: number; upper: number; coverage: number; empirical_coverage?: number | null } | null
})
export type MoistureLayer = {
  field_id: string
  season: number
  demo: boolean
  date: string
  depth_cm: [number, number]
  unit: string
  minimum: number
  maximum: number
  cells: { geometry: Polygon; value: number }[]
  source?: MoistureProvenance | null
}

export type MoistureForecast = {
  product: 'moisture_forecast'
  field_id: string
  initialized_at: string
  fetched_at: string
  model_version: string | null
  provider: string
  source_url: string
  resolution_km: number
  unit: string
  days: {
    date: string
    lead_days: number
    value: number
    ifs_value: number
    precipitation_mm: number
    et0_mm: number
    interval: { lower: number; upper: number; empirical_coverage: number | null } | null
  }[]
}

export type FarmDefaults = {
  irrigation_available: boolean
  priority: string
  equipment: string | null
  budget: number | null
}
export type ObservationCategory = 'general' | 'crop_condition' | 'weeds' | 'pests' | 'disease' | 'water' | 'soil'
export type Observation = {
  id: number
  field_id: string
  kind: 'soil_photo' | 'field_note'
  text: string
  date: string
  category?: ObservationCategory
  latitude?: number
  longitude?: number
  has_photo?: boolean
}
export type NewObservation = Pick<Observation, 'field_id' | 'kind' | 'text' | 'category' | 'latitude' | 'longitude'> & {
  date?: string
  photo_jpeg?: string
  client_id?: string
}
export type Farm = {
  name: string
  season: number
  total_area_ha: number
  defaults: FarmDefaults
  observations: Observation[]
}

export type FieldContext = { field_id: string; season: number; scenario: Scenario }
export type WaterDay = {
  date: string
  reserve_mm: number
  opening_mm: number
  rain_mm: number
  irrigation_mm: number
  water_use_mm: number
  drainage_mm: number
  source_rain_mm?: number | null
  reference_et0_mm?: number | null
}
export type WaterResult = FieldContext & {
  demo: boolean
  source?: 'demo' | 'historical'
  provenance?: WaterProvenance | null
  threshold_mm: number
  flowering_start: string | null
  flowering_end: string | null
  first_stress_date: string | null
  min_reserve_mm: number
  stress_days: number
  days: WaterDay[]
}

export type WaterProvenance = {
  weather_source: string
  weather_model: string
  weather_url: string
  grid_resolution_degrees: number
  timezone: string
  start_date: string
  end_date: string
  rainfall_multiplier: number
  initial_reserve_mm: number
  initial_reserve_source: string
  capacity_mm: number
  root_depth_m: number
  model_version: string
  limitations: string[]
}

export type WaterForecast = {
  analysis: 'water_forecast'
  field_id: string
  season: number
  crop: string
  initialized_at: string
  generated_at: string
  method: 'fao56' | 'ml'
  model_version: string | null
  physical_model_version: string
  provider: string
  source_url: string
  grid_resolution_km: number
  root_depth_m: number
  initial_reserve_mm: number
  capacity_mm: number
  threshold_mm: number
  field_capacity: number
  wilting_point: number
  crop_coefficient: number
  limitations: string[]
  days: (WaterDay & {
    lead_days: number
    root_zone_estimate_mm: number | null
  })[]
}

export type JobStatus = 'queued' | 'running' | 'retrying' | 'succeeded' | 'failed'
export type JobKind = 'simulation' | 'geo_ml'
type JobAnalysis = 'water_balance' | 'water_forecast' | 'rotation_comparison' | 'vegetation' | 'terrain'
export type JobOptions = {
  source?: MoistureSource | 'forecast'
  analysis?: JobAnalysis
  latest?: true
  sample_date?: string
  start_date?: string
  end_date?: string
  initial_reserve_mm?: number
  flowering_start?: string
  flowering_end?: string
}
export type Job = {
  boundary_revision?: number
  id: string
  kind: JobKind
  analysis?: JobAnalysis | null
  field_id: string
  status: JobStatus
  attempts: number
  result: WaterResult | WaterForecast | MoistureLayer | MoistureForecast | HistoricalRotationComparison | null
  error: string | null
  created_at: string
  updated_at: string
}

export type RotationPlan = {
  id: string
  name: string
  crops: { year: number; crop: string }[]
  stress_days: number
  longest_episode: number
  sensitive_days: number
}
export type Rotations = { plans: RotationPlan[]; main_plan_id: string | null; demo: boolean }
export type RotationYield = {
  field_id: string
  season: number
  model_version: string | null
  status: 'validated' | 'experimental' | 'unavailable'
  source_url: string | null
  training_sites: number | null
  plans: {
    id: 'plan-a' | 'plan-b'
    estimates: {
      year: number
      crop: string
      previous_crop: string | null
      change_t_ha: number | null
      lower_t_ha: number | null
      upper_t_ha: number | null
      reason: string | null
    }[]
  }[]
}
export type HistoricalRotationComparison = FieldContext & {
  analysis: 'rotation_comparison'
  demo: false
  evaluated_year: number
  plans: (Omit<RotationPlan, 'sensitive_days'> & {
    sensitive_days: number | null
    min_reserve_mm: number
    provenance: WaterProvenance
  })[]
}
export type AgentProvider = 'template' | 'scripted-llm' | 'gemini' | 'anthropic' | 'openai' | 'deterministic'
export type AgentStatus = { provider: AgentProvider | null; demo: boolean; available: boolean }
export type AgentCitation = { id: string; title: string; url: string; publisher?: string | null; year?: number | null }
export type AgentAction = {
  label: string
  target: 'water' | 'rotations' | 'fields'
  run_id?: string | null
  analysis?: 'vegetation' | null
  forecast_day?: string | null
  layer?: 'ndvi_variability' | null
  field_revision?: number | null
}
type AgentMessageMetadata = {
  demo: boolean
  actions: AgentAction[]
  provider?: AgentProvider | null
  citations?: AgentCitation[]
  options?: string[]
}
export type AgentReply = AgentMessageMetadata & { message: string; conversation_id: string }
export type ConversationMessage = AgentMessageMetadata & { id: number; role: 'user' | 'assistant'; text: string; created_at: string }
export type Conversation = FieldContext & { id: string; title: string; updated_at: string }
export type ConversationDetail = Conversation & {
  messages: ConversationMessage[]
}

/** A farmer's recorded decision about a planning condition the assistant proposed (agent contract › Constraint).
 *  Built by the API from its own record for one field, season and boundary; proposals are never recorded. */
export type PlanningConstraint = {
  id: string
  type: 'irrigation_available' | 'fertilizer_budget' | 'erosion_protection' | 'rotation_length_years'
    | 'excluded_crops' | 'preferred_crops' | 'previous_crop' | 'organic_only' | 'machinery_limit' | 'market_preference'
  value: boolean | number | string | string[]
  status: 'confirmed' | 'rejected'
  source: 'farmer_text' | 'farmer_voice' | 'farmer_ui' | 'agent_inferred'
  evidence?: string
  confidence?: number
  /** Opaque and new with every write: a replacement or a removal names the exact decision the farmer saw, even
   *  if one of the same kind was removed and decided again meanwhile. */
  token: string
}

/** A proposed value that would change an earlier decision: not saved until the farmer confirms the replacement. */
export type ConstraintReplacement = {
  current: PlanningConstraint
  proposed: Omit<PlanningConstraint, 'token'> & { status: 'confirmed' | 'rejected' }
}

/** The farmer's decision: the widget's compare.set_constraints envelope, plus each earlier decision (type and
 *  token) the farmer saw and chose to replace. */
export type ConstraintDecision = {
  field_id: string
  envelope: unknown
  replaces?: { type: PlanningConstraint['type']; token: string }[]
}
