import { request as apiFetch } from './client'

export type HandoffOperation = 'forecast' | 'climate_step' | 'climate_compare' | 'assessment' | 'compare_water_balances' | 'sutherland_2027'
export interface HandoffSubmission {
  request_id: string
  geometry_revision: number
  operation: HandoffOperation
  parameters: Record<string, unknown>
  allow_public_data: boolean
}
export interface HandoffCrop {
  id: string
  name: string
  climate_supported: boolean
  strategy_supported?: boolean
  allocation_crop_id: string | null
  profile: {
    calendars?: Array<{ id: string; stage_lengths: number[]; reference_planting_months: string; reference_region: string | null }>
    default_calendar_id?: string | null
    coefficients?: number[]
    root_depth_m?: number
    [key: string]: unknown
  }
}
export interface HandoffCatalog {
  status: 'available' | 'unavailable'
  engine_version: string
  source_commit: string
  empirical_status: 'not_validated'
  operations: HandoffOperation[]
  crops: HandoffCrop[]
  request_schemas: Record<string, unknown>
  equipment?: Array<{ id: string; name: string; purpose: string }>
  cultivar_references?: Array<Record<string, unknown>>
  limitations: string[]
  reasons: string[]
}
export interface HandoffResult {
  operation: HandoffOperation
  status: 'available' | 'unavailable' | 'unsupported'
  data_kind: 'historical_replay' | 'hybrid_scenario' | 'conditional_climate' | 'supplied_balance'
  empirical_status: 'not_validated'
  engine_version: string
  source_commit: string
  input_sha256: string
  evidence_sha256: string
  acquired_at: string
  ranking_usable: boolean
  suitability_status: 'not_assessed' | 'arable_land_only' | 'review_required' | 'no_thermal_growth'
  diagnostics: Array<{ code: string; severity: 'info' | 'warning' | 'blocking'; message: string; crop_id: string | null; start_date: string | null; end_date: string | null; details: Record<string, unknown> }>
  payload: Record<string, unknown>
  source_receipts: Array<{ source_url: string; sha256: string; bytes: number; available_at: string; kind: 'acquired_public_input' | 'packaged_reference' }>
  limitations: string[]
  reasons: string[]
}
export interface HandoffJob {
  job_id: string
  field_id: string
  geometry_revision: number
  status: 'queued' | 'running' | 'retrying' | 'succeeded' | 'failed'
  attempts: number
  result: HandoffResult | null
  error: string | null
}
export type StrategyMode = 'sequence' | 'compare'
export type StrategyPriority = 'water_stress' | 'water_deficit' | 'water_saving' | 'yield' | 'revenue' | 'revenue_after_seed' | 'soil_cover'
export type StrategyIrrigation = { status: 'unknown' | 'none' | 'available'; daily_m3?: number; method?: string }
export type StrategyChoice = { job_id: string; crop_id: string }
export type StrategyContext = {
  field_id: string; geometry_revision: number; area_ha: number
  min_year: number; max_year: number; min_month: number; max_month: number; max_steps: number; max_calendar_years: number
  history: { crop_id: string | null; sowing_date: string | null; season_year: number }[]
}
export type StrategySubmission = {
  request_id: string; geometry_revision: number; mode: StrategyMode; planting_date: string
  step_count: number; priority: StrategyPriority; previous: StrategyChoice[]
  comparison_crops?: [string, string]; history_crop_id?: string; history_sowing_date?: string
  irrigation: StrategyIrrigation
}
export type StrategyCandidate = {
  crop_id: string; choice_id?: string; rank: number | null; status: 'ok' | 'unsupported' | 'unavailable'; reason?: string | null
  warnings?: string[]; blockers?: string[]
  water?: Record<string, number | null>; economics?: Record<string, unknown>
  agronomy?: Record<string, unknown>; scenarios?: Record<string, unknown>; erosion?: Record<string, unknown>
}
export type StrategyScenario = {
  id: string; field_id: string; geometry_revision: number; created_at: string
  priority: StrategyPriority; irrigation: StrategyIrrigation; assumptions: string[]
  choices: (StrategyChoice & { planting_date: string; metrics: StrategyCandidate })[]
}

const strategyPath = (fieldId: string) => `/rotations/handoff/fields/${encodeURIComponent(fieldId)}`
export const rotationStrategyApi = {
  context: (fieldId: string, signal?: AbortSignal) => apiFetch<StrategyContext>(`${strategyPath(fieldId)}/strategy-context`, { signal }),
  submit: (fieldId: string, body: StrategySubmission, signal?: AbortSignal) => apiFetch<HandoffJob>(`${strategyPath(fieldId)}/strategy-jobs`, { method: 'POST', body: JSON.stringify(body), signal }),
  scenarios: (fieldId: string, offset: number, limit: number, signal?: AbortSignal) =>
    apiFetch<StrategyScenario[]>(`${strategyPath(fieldId)}/strategy-scenarios?offset=${offset}&limit=${limit}`, { signal }),
  save: (fieldId: string, geometryRevision: number, choices: StrategyChoice[], signal?: AbortSignal) => apiFetch<StrategyScenario>(`${strategyPath(fieldId)}/strategy-scenarios`, {
    method: 'POST', body: JSON.stringify({ geometry_revision: geometryRevision, choices }), signal,
  }),
}

export const rotationHandoffApi = {
  jobs: (fieldId: string, signal?: AbortSignal) => apiFetch<HandoffJob[]>(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/jobs`, { signal }),
  catalog: (signal?: AbortSignal) => apiFetch<HandoffCatalog>('/rotations/handoff/catalog', { signal }),
  submit: (fieldId: string, body: HandoffSubmission, signal?: AbortSignal) => apiFetch<HandoffJob>(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/jobs`, { method: 'POST', body: JSON.stringify(body), signal }),
  job: (fieldId: string, jobId: string, signal?: AbortSignal) => apiFetch<HandoffJob>(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/jobs/${encodeURIComponent(jobId)}`, { signal }),
}

export interface HandoffSeasonalWaterRow {
  job_id: string
  scenario_id: string
  plan_id: string
  crop_id: string
  start_date: string
  end_date: string
  data_kind: string
  evidence_sha256: string
  input_sha256?: string | null
  engine_version?: string | null
  water_shortfall_pct: number | null
  demand_mm: number | null
  use_mm: number | null
  deficit_mm: number | null
  drought_days: number | null
  crop_days: number | null
  opening_mm: number | null
  closing_mm: number | null
  irrigation_mm: number | null
  drainage_mm: number | null
  waterlogging_days: number | null
  water_stress_days: number | null
}
export interface HandoffSeasonalWater { field_id: string; geometry_revision: number; rows: HandoffSeasonalWaterRow[] }
export function handoffSeasonalWater(fieldId: string, signal?: AbortSignal): Promise<HandoffSeasonalWater> {
  return apiFetch(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/seasonal-water`, { signal })
}
