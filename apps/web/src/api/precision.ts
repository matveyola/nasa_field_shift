import type { Polygon, Position } from './contracts'
import { request } from './client'

export type PrecisionOperation = 'fertilization' | 'sowing' | 'spraying'
export type PrecisionGeometry = { type: 'Polygon'; coordinates: Position[][] } | { type: 'MultiPolygon'; coordinates: Position[][][] }
export interface PrecisionFeature {
  type: 'Feature'; geometry: PrecisionGeometry
  properties: { zone: string; rate: number; area_ha: number; treatment?: string | null; block?: number | null; plot_id?: string | null }
}
export interface HarvestReport {
  sample_count: number; excluded_samples: number; weighting: string; baseline: string
  plots: (PrecisionFeature['properties'] & { count: number; yield_t_ha: number | null; sampled_area_ha: number | null })[]
  treatments: { treatment: string; rate: number; sample_count: number; complete_blocks: number; paired_blocks: number; yield_t_ha: number | null; difference_t_ha: number | null; difference_standard_error: number | null }[]
}
export interface PrecisionPlan {
  id: string; field_id: string; season: number; kind: 'trial' | 'prescription'; name: string
  status: 'draft' | 'approved'; version: number; stale: boolean; created_at: string
  operation: PrecisionOperation; product: string; unit: string; area_ha: number; has_harvest: boolean
  snapshot?: { footprint: Polygon; features: PrecisionFeature[]; fallback_rate: number; provenance: Record<string, unknown> }
  harvest?: HarvestReport | null; harvest_name?: string | null
}
export interface PrecisionContext { boundary_revision: number; geometry_fingerprint: string; geometry: Polygon }
const base = (field: string) => '/fields/' + encodeURIComponent(field) + '/precision'
export const precisionApi = {
  context: (field: string, season: number, signal: AbortSignal) => request<PrecisionContext>(base(field) + '/context?season=' + season, { signal }),
  list: (field: string, season: number, signal: AbortSignal) => request<PrecisionPlan[]>(base(field) + '?season=' + season, { signal }),
  detail: (field: string, id: string, signal: AbortSignal) => request<PrecisionPlan>(base(field) + '/' + id, { signal }),
  create: (field: string, kind: 'trials' | 'prescriptions', body: unknown) => request<PrecisionPlan>(base(field) + '/' + kind, { method: 'POST', body: JSON.stringify(body) }),
  approve: (field: string, id: string, version: number, zero: boolean) => request<PrecisionPlan>(base(field) + '/' + id + '/approve', { method: 'POST', body: JSON.stringify({ version, confirmed_rates_and_geometry: true, confirmed_zero_rates: zero }) }),
  harvest: (field: string, id: string, body: { version: number; file_name: string; csv: string }) => request<PrecisionPlan>(base(field) + '/' + id + '/harvest', { method: 'POST', body: JSON.stringify(body) }),
  async download(field: string, id: string, format: 'shapefile' | 'isoxml' | 'geojson' | 'csv') {
    const response = await fetch((import.meta.env.VITE_API_BASE ?? '/api') + base(field) + '/' + id + '/export/' + format)
    if (!response.ok) {
      const result = await response.json().catch(() => null)
      throw new Error(typeof result?.detail === 'string' ? result.detail : 'Export failed.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'FieldShift-' + id.slice(0, 8) + '-' + format + (format === 'csv' ? '.csv' : format === 'geojson' ? '.geojson' : '.zip')
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}
