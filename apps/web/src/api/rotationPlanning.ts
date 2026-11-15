import { request } from './client'

export type PlanningCrop = 'Wheat' | 'Maize' | 'Soy' | 'Barley' | 'Sunflower' | 'Rapeseed' | 'Peas'
export type CropRule = {
  crop: PlanningCrop; min_break_years: number; preference: number
  max_share_percent: number; min_area_ha: number; max_area_ha: number | null
}
export type CropDecision = { field_id: string; season: number; crop: PlanningCrop }
export type PlanningRequest = {
  client_id: string; name: string; start_season: number; season_count?: number; field_ids: string[]
  crops: CropRule[]; history: CropDecision[]; locked: CropDecision[]
  avoid_consecutive_family: boolean; require_two_crops: boolean
}
export type PlanningField = {
  id: string; name: string; area_ha: number; boundary_revision: number
  crop: string; crop_season: number; history: CropDecision[]
}
export type PlanningContext = {
  start_season: number; season_count: number; min_season_count: 1; max_season_count: 6; fields: PlanningField[]; crops: { crop: PlanningCrop; family: string }[]
}
export type PlannerDiagnostic = { code: string; message: string; field_id: string | null; crop: string | null; season: number | null }
export type PlanningStatus = 'optimal' | 'feasible' | 'infeasible' | 'search_limit'
export type PlanningResult = {
  status: PlanningStatus
  assignments: { field_id: string; season: number; crop: PlanningCrop; area_ha: number; locked: boolean; reasons: string[] }[]
  areas: { season: number; crop: PlanningCrop; area_ha: number; share_percent: number; min_area_ha: number; max_area_ha: number }[]
  diagnostics: PlannerDiagnostic[]; warnings: PlannerDiagnostic[]
  explored_nodes: number; node_limit: number;
  generation_nodes: number; generation_limit: number; generation_complete: boolean; time_limit_seconds: number; objective: number | null
  objective_unit: string; method: string
}
export type PlanningDraft = {
  id: string; name: string; start_season: number; season_count: number; created_at: string; applied_at: string | null
  request: PlanningRequest; fields: PlanningField[]; result: PlanningResult
}
export type PlanningSummary = Pick<PlanningDraft, 'id' | 'name' | 'start_season' | 'season_count' | 'created_at' | 'applied_at'> & {
  status: PlanningStatus; field_count: number
}

export const rotationPlanningApi = {
  context: (season: number, signal?: AbortSignal, seasonCount = 3) => request<PlanningContext>('/rotations/planning/context?start_season=' + season + '&season_count=' + seasonCount, { signal }),
  drafts: (signal?: AbortSignal) => request<PlanningSummary[]>('/rotations/planning/drafts', { signal }),
  draft: (id: string, signal?: AbortSignal) => request<PlanningDraft>('/rotations/planning/drafts/' + encodeURIComponent(id), { signal }),
  generate: (body: PlanningRequest) => request<PlanningDraft>('/rotations/planning/drafts', { method: 'POST', body: JSON.stringify(body) }),
  apply: (id: string) => request<PlanningDraft>('/rotations/planning/drafts/' + encodeURIComponent(id) + '/apply', { method: 'POST' }),
}
