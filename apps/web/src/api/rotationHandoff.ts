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
export const rotationHandoffApi = {
  catalog: (signal?: AbortSignal) => apiFetch<HandoffCatalog>('/rotations/handoff/catalog', { signal }),
  submit: (fieldId: string, body: HandoffSubmission, signal?: AbortSignal) => apiFetch<HandoffJob>(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/jobs`, { method: 'POST', body: JSON.stringify(body), signal }),
  job: (fieldId: string, jobId: string, signal?: AbortSignal) => apiFetch<HandoffJob>(`/rotations/handoff/fields/${encodeURIComponent(fieldId)}/jobs/${encodeURIComponent(jobId)}`, { signal }),
}
