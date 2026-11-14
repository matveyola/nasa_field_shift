import { request } from './client'

export type PlanningCrop = 'Wheat' | 'Maize' | 'Soy' | 'Barley' | 'Sunflower' | 'Rapeseed' | 'Peas'
export type CropRule = {
  crop: PlanningCrop; min_break_years: number; preference: number
  max_share_percent: number; min_area_ha: number; max_area_ha: number | null
}
export type CropDecision = { field_id: string; season: number; crop: PlanningCrop }
export type PlanningRequest = {
  client_id: string; name: string; start_season: number; field_ids: string[]
  crops: CropRule[]; history: CropDecision[]; locked: CropDecision[]
  avoid_consecutive_family: boolean; require_two_crops: boolean
}
export type PlanningField = {
  id: string; name: string; area_ha: number; boundary_revision: number
  crop: string; crop_season: number; history: CropDecision[]
  allowed_crops?: PlanningCrop[] | null
  priority?: 'water' | 'soil' | 'low_change' | null
}
export type PlanningContext = {
  start_season: number; fields: PlanningField[]; crops: { crop: PlanningCrop; family: string }[]
}
export type PlannerDiagnostic = { code: string; message: string; field_id: string | null; crop: string | null; season: number | null }
export type PlanningStatus = 'optimal' | 'feasible' | 'infeasible' | 'search_limit'
export type PlanningResult = {
  status: PlanningStatus
  assignments: { field_id: string; season: number; crop: PlanningCrop; area_ha: number; locked: boolean; reasons: string[] }[]
  areas: { season: number; crop: PlanningCrop; area_ha: number; share_percent: number; min_area_ha: number; max_area_ha: number }[]
  diagnostics: PlannerDiagnostic[]; warnings: PlannerDiagnostic[]
  explored_nodes: number; node_limit: number; objective: number | null
  objective_unit: string; method: string
}
export type PlanningDraft = {
  id: string; name: string; start_season: number; created_at: string; applied_at: string | null
  request: PlanningRequest; fields: PlanningField[]; result: PlanningResult
}
export type PlanningSummary = Pick<PlanningDraft, 'id' | 'name' | 'start_season' | 'created_at' | 'applied_at'> & {
  status: PlanningStatus; field_count: number
}

export const rotationPlanningApi = {
  context: (season: number, signal?: AbortSignal) => request<PlanningContext>('/rotations/planning/context?start_season=' + season, { signal }),
  drafts: (signal?: AbortSignal) => request<PlanningSummary[]>('/rotations/planning/drafts', { signal }),
  draft: (id: string, signal?: AbortSignal) => request<PlanningDraft>('/rotations/planning/drafts/' + encodeURIComponent(id), { signal }),
  generate: (body: PlanningRequest) => request<PlanningDraft>('/rotations/planning/drafts', { method: 'POST', body: JSON.stringify(body) }),
  apply: (id: string) => request<PlanningDraft>('/rotations/planning/drafts/' + encodeURIComponent(id) + '/apply', { method: 'POST' }),
}
