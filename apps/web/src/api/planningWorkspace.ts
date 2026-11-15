import { planningTransport } from './planningV1'
import { decode } from '../features/planning/contracts'
import type { CropRef, FarmPlanningRequest, FieldProfileSnapshot, ModelCard } from './planningV1.generated'
import type { PlanningEvidence } from '../features/planning/model'

export interface PlanningBootstrap {
  farm_id: string
  profiles: FieldProfileSnapshot[]
  missing_profiles: { field_id: string; name: string; code: string }[]
  request: FarmPlanningRequest | null
  crop_options: CropRef[]
  model_card: ModelCard | null
  configuration: string | null
  status: string
  reason: string
}
export async function planningBootstrap(signal?: AbortSignal): Promise<PlanningBootstrap> {
  const result = await planningTransport<PlanningBootstrap>('/planning-workspace', { signal })
  if (!result || typeof result.farm_id !== 'string' || !Array.isArray(result.profiles) || !Array.isArray(result.missing_profiles)) throw new Error('Invalid planning workspace')
  result.profiles = result.profiles.map(profile => decode('field_profile', profile))
  if (result.request) result.request = decode('request', result.request)
  if (result.profiles.some(p => p.farm_id !== result.farm_id) || result.request && result.request.farm_id !== result.farm_id) throw new Error('Planning workspace scope mismatch')
  return result
}
export async function planningEvidence(farm: string, run: string, signal: AbortSignal): Promise<PlanningEvidence> {
  const result = await planningTransport<PlanningEvidence>(`/planning-workspace/farms/${encodeURIComponent(farm)}/runs/${encodeURIComponent(run)}/evidence`, { signal })
  return { candidates: result.candidates.map(v => decode('candidate', v)), evaluations: result.evaluations.map(v => decode('evaluation', v)) }
}
export function initializePlanningProfile(field: string, refreshBoundary = false): Promise<FieldProfileSnapshot> {
  return planningTransport(`/planning-workspace/fields/${encodeURIComponent(field)}/initialize${refreshBoundary ? "?refresh_boundary=true" : ""}`, { method: 'POST' })
}
