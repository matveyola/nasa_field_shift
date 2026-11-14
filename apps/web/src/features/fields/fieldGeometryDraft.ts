import type { Field, Position } from '../../api/contracts'
import { maxFieldVertices } from './geometry'
import type { BoundarySuggestion } from './useBoundarySuggestion'

export type FieldEditMode = 'boundary' | 'planting'

type DraftShape = {
  points: Position[]
  append: boolean
  active?: number
}

export type FieldGeometryDraft = DraftShape & {
  mode: FieldEditMode
  fieldId?: string
  revision: number
  effectiveSeason?: number
  catalogue?: { year: number; source: string; sourceUrl: string }
  fullField: boolean
  undo: DraftShape[]
  suggestion?: Omit<BoundarySuggestion, 'points'>
}

export function fieldGeometryDraft(field: Field | undefined, mode: FieldEditMode): FieldGeometryDraft {
  const geometry = mode === 'boundary' ? field?.geometry : field?.planting?.geometry
  const points = geometry?.coordinates[0].slice(0, -1) ?? []
  return {
    mode, fieldId: field?.id, revision: field?.boundary_revision ?? 0, points, append: !points.length,
    fullField: false, undo: [],
  }
}

export function recordShape(draft: FieldGeometryDraft, shape: DraftShape): FieldGeometryDraft {
  if (shape.points.length > maxFieldVertices) return draft
  if (draft.append === shape.append && draft.points.length === shape.points.length
    && draft.points.every(([lon, lat], i) => lon === shape.points[i][0] && lat === shape.points[i][1])) return draft
  const { points, append, active } = draft
  return { ...draft, ...shape, active: shape.active, undo: [...draft.undo.slice(-29), { points, append, active }] }
}

export function undoShape(draft: FieldGeometryDraft): FieldGeometryDraft {
  const previous = draft.undo.at(-1)
  return previous ? { ...draft, ...previous, undo: draft.undo.slice(0, -1) } : draft
}
