import { request } from './client'
import type { Polygon } from './contracts'

export type BoundaryVersion = { id: number; revision: number; effective_season: number; geometry: Polygon; area_ha: number; created_at: string; source: string }
export type PreparedContour = { id: string; geometry: Polygon; area_ha: number; year: number; source: string; source_url: string; license: string; region: string }
export type ContourCoverage = { region: string; year: number; source_url: string; license: string; count: number; west: number; east: number; south: number; north: number }

export const boundaryApi = {
  history: (fieldId: string, signal?: AbortSignal) => request<BoundaryVersion[]>(`/fields/${encodeURIComponent(fieldId)}/boundary-history`, { signal }),
  coverage: (signal?: AbortSignal) => request<ContourCoverage[]>('/fields/prepared-contours/coverage', { signal }),
  contours: (bbox: { west: number; south: number; east: number; north: number }, signal?: AbortSignal) => request<{ contours: PreparedContour[]; truncated: boolean }>('/fields/prepared-contours?' + new URLSearchParams(Object.entries(bbox).map(([key, value]) => [key, String(value)])), { signal }),
}
