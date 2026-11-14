import type { Field, WaterForecast } from '../../api/contracts'
import { fieldWaterPriority, type FieldWaterPriority } from './fieldPriority.ts'

export type FarmPriorityEntry = { field: Field; priority: FieldWaterPriority }

/** Bound requests on farms with many fields; caller owns cancellation and refresh cadence. */
export async function loadFarmForecasts(
  fields: readonly Field[], today: string, signal: AbortSignal,
  fetchForecast: (fieldId: string, signal: AbortSignal) => Promise<WaterForecast | null>,
): Promise<FarmPriorityEntry[]> {
  const entries: FarmPriorityEntry[] = new Array(fields.length)
  let cursor = 0
  async function worker() {
    while (cursor < fields.length && !signal.aborted) {
      const index = cursor++
      const field = fields[index]
      try {
        const forecast = await fetchForecast(field.id, signal)
        entries[index] = { field, priority: fieldWaterPriority(forecast, field.id, today) }
      } catch (error) {
        if (signal.aborted) throw error
        entries[index] = { field, priority: { kind: 'unavailable', fieldId: field.id, reason: 'request_failed' } }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(3, fields.length) }, worker))
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  return entries
}
