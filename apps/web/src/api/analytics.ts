import { request } from './client'
import type { Job, Polygon, Position } from './contracts'
export type SpectralIndex = 'ndvi' | 'msavi' | 'ndre' | 'reci' | 'mcari' | 'ndmi' | 'ndwi'
export type ExtraIndex = Exclude<SpectralIndex, 'ndvi' | 'msavi'>
export type SpectralSummary = { mean: number; p10: number; p90: number; valid_percent: number; pixel_count: number }

export type VegetationObservation = {
  date: string
  scene_id: string
  mean_ndvi: number
  p10_ndvi: number
  p90_ndvi: number
  valid_percent: number
  pixel_count: number
  catalog_url: string
  mean_msavi?: number | null
  indices?: Partial<Record<ExtraIndex, SpectralSummary>>
}
export type VegetationResult = {
  field_id: string
  season: number
  boundary_revision: number
  geometry_fingerprint: string
  boundary_geometry?: Polygon | null
  boundary_history_assumed?: boolean
  generated_at: string | null
  status: 'ready' | 'partial' | 'unavailable'
  sampling: 'monthly_clear_scene'
  resolution_m: number
  observations: VegetationObservation[]
  heterogeneity: {
    date: string
    median_ndvi: number
    spread_ndvi: number
    low_area_percent: number
    valid_percent: number
    resolution_m: number
    low_threshold?: number
    high_threshold?: number
    cells: { geometry: Polygon; value: number; zone: 'low' | 'typical' | 'high'; msavi?: number | null; indices?: Partial<Record<ExtraIndex, number>> }[]
  } | null
  months: { month: number; status: 'ready' | 'no_clear_scene' | 'pending' | 'missing' | 'failed' | 'unsupported'; reason: string | null }[]
  source_url: string
  limitations: string[]
}
export type VegetationSceneResult = Pick<VegetationResult, 'field_id' | 'season' | 'boundary_revision' | 'geometry_fingerprint'> & {
  observation: VegetationObservation
  heterogeneity: NonNullable<VegetationResult['heterogeneity']>
}
export type TerrainResult = {
  analysis: 'terrain'
  field_id: string
  boundary_revision: number
  generated_at: string
  status: 'ready' | 'unavailable'
  reason: string | null
  source: string
  source_url: string
  source_resolution_m: number
  model_version: string
  attribution: string
  method: 'local_surface_gradient'
  vectors: { start: Position; end: Position; slope_percent: number; elevation_m: number }[]
  elevation_min_m: number | null
  elevation_max_m: number | null
  coverage_percent: number
  limitations: string[]
}

export const analyticsApi = {
  vegetation: (fieldId: string, season: number, signal?: AbortSignal, includeCells = true) =>
    request<VegetationResult>('/fields/' + encodeURIComponent(fieldId) + '/vegetation?season=' + season + '&include_cells=' + includeCells, { signal }),
  vegetationScene: (fieldId: string, season: number, date: string, sceneId: string, signal?: AbortSignal) =>
    request<VegetationSceneResult | null>('/fields/' + encodeURIComponent(fieldId) + '/vegetation/scene?' + new URLSearchParams({ season: String(season), observation_date: date, scene_id: sceneId }), { signal }),
  refreshVegetation: (fieldId: string, season: number, signal?: AbortSignal) =>
    request<{ jobs: Job[] }>('/fields/' + encodeURIComponent(fieldId) + '/vegetation/refresh?season=' + season, { method: 'POST', signal }),
  terrain: (fieldId: string, signal?: AbortSignal) =>
    request<TerrainResult | null>('/fields/' + encodeURIComponent(fieldId) + '/terrain', { signal }),
  refreshTerrain: (fieldId: string, signal?: AbortSignal) =>
    request<Job>('/fields/' + encodeURIComponent(fieldId) + '/terrain/refresh', { method: 'POST', signal }),
}
