import type { VegetationResult } from '../../api/analytics'
import type { Field } from '../../api/contracts'
import { vegetationInsight } from '../fields/vegetationInsights.ts'
import { rankFieldWaterPriorities } from './fieldPriority.ts'
import type { FarmPriorityEntry } from './loadFarmForecasts.ts'

export type FarmVegetationSignal = ReturnType<typeof vegetationInsight>

export async function loadFarmVegetation(fields: readonly Field[], season: number, today: string, signal: AbortSignal,
  fetchVegetation: (fieldId: string, season: number, signal: AbortSignal) => Promise<VegetationResult>,
) {
  const result: Record<string, FarmVegetationSignal> = {}
  let cursor = 0
  async function worker() {
    while (cursor < fields.length && !signal.aborted) {
      const field = fields[cursor++]
      try {
        const data = await fetchVegetation(field.id, season, signal)
        result[field.id] = data.field_id === field.id && data.boundary_revision === field.boundary_revision && data.season === season
          ? vegetationInsight(data, today) : undefined
      } catch (error) {
        if (signal.aborted) throw error
        result[field.id] = undefined
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(3, fields.length) }, worker))
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  return result
}

export function rankCombinedPriorities(entries: readonly FarmPriorityEntry[], vegetation: Record<string, FarmVegetationSignal>) {
  const waterSorted = rankFieldWaterPriorities(entries)
  return waterSorted.map((entry, order) => ({ ...entry, order })).sort((a, b) => {
    const score = (entry: FarmPriorityEntry) => entry.priority.kind === 'shortage' ? 0
      : vegetation[entry.field.id]?.inspect ? 1 : entry.priority.kind === 'unavailable' ? 2 : 3
    return score(a) - score(b) || a.order - b.order
  })
}
