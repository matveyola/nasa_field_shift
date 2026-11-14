import type { VegetationResult } from '../../api/analytics'
import type { Field, WaterForecast } from '../../api/contracts'
import { fieldWaterPriority } from '../farm/fieldPriority.ts'
import { vegetationInsight } from '../fields/vegetationInsights.ts'

type NotificationBase = {
  id: string
  fieldId: string
  fieldName: string
  boundaryRevision: number
  season: number
  date: string
  source: string
}

export type FieldNotification = NotificationBase & (
  | { kind: 'water'; minimumMm: number; thresholdMm: number }
  | { kind: 'vegetation'; lowAreaPercent: number }
)

export type NotificationSnapshot = { items: FieldNotification[]; unavailableFields: number }
export type NotificationSources = {
  /** GET-only endpoint must select saved jobs for the current field boundary revision. */
  waterForecast: (fieldId: string, signal: AbortSignal) => Promise<WaterForecast | null>
  /** GET-only endpoint must select the current planting footprint, with spatial cells omitted. */
  vegetation: (fieldId: string, season: number, signal: AbortSignal) => Promise<VegetationResult>
}

function isoDay(value: string) {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
}

function isDemo(value: object) {
  return 'demo' in value && value.demo === true
}

function validVegetation(data: VegetationResult, field: Field, season: number, today: string) {
  // The API already filters persisted jobs by geometry fingerprint. Re-check returned identity
  // and metadata; the caller cancels the batch when its field/planting snapshot changes.
  const layer = data.heterogeneity
  return !isDemo(data) && data.field_id === field.id && data.boundary_revision === field.boundary_revision
    && data.season === season && data.status !== 'unavailable' && Boolean(data.geometry_fingerprint)
    && Boolean(data.generated_at) && Number.isFinite(Date.parse(data.generated_at!))
    && Date.parse(data.generated_at!) < Date.parse(today) + 86_400_000
    && layer !== null && isoDay(layer.date) && Number(layer.date.slice(0, 4)) === season
    && Number.isFinite(layer.valid_percent) && layer.valid_percent >= 70 && layer.valid_percent <= 100
    && Number.isFinite(layer.median_ndvi) && layer.median_ndvi >= -1 && layer.median_ndvi <= 1
}

/** Saved evidence only. Never schedules jobs, satellite downloads, or model requests.
 * At most three HTTP requests run at once; each worker requests its two sources sequentially.
 * A failed/missing source is reported separately and never becomes an invented alert.
 */
export async function loadFieldNotifications(
  fields: readonly Field[], season: number, today: string, signal: AbortSignal, sources: NotificationSources,
): Promise<NotificationSnapshot> {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  if (!isoDay(today) || season !== Number(today.slice(0, 4))) {
    return { items: [], unavailableFields: fields.length }
  }
  const results: { items: FieldNotification[]; incomplete: boolean }[] = new Array(fields.length)
  let cursor = 0
  async function worker() {
    while (cursor < fields.length && !signal.aborted) {
      const index = cursor++
      const field = fields[index]
      const base = { fieldId: field.id, fieldName: field.name, boundaryRevision: field.boundary_revision, season }
      const items: FieldNotification[] = []
      let incomplete = false
      try {
        const forecast = await sources.waterForecast(field.id, signal)
        if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
        // Current API gates revision in its DB query. Also reject explicit mismatches if a
        // future adapter includes revision metadata, without fabricating one for old data.
        const wrongRevision = forecast && 'boundary_revision' in forecast
          && forecast.boundary_revision !== field.boundary_revision
        const priority = fieldWaterPriority(wrongRevision ? null : forecast, field.id, today)
        incomplete ||= priority.kind === 'unavailable'
        if (priority.kind === 'shortage' && forecast && forecast.season === season) {
          const date = priority.firstStressDate!
          items.push({ ...base, id: JSON.stringify(['water', field.id, season, field.boundary_revision, date]),
            kind: 'water', date, source: `FAO-56 · ${forecast.provider}`,
            minimumMm: priority.minimumMm, thresholdMm: priority.thresholdMm })
        }
      } catch (error) {
        if (signal.aborted) throw error
        incomplete = true
      }
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
      try {
        const vegetation = await sources.vegetation(field.id, season, signal)
        if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
        const insight = validVegetation(vegetation, field, season, today) ? vegetationInsight(vegetation, today) : undefined
        incomplete ||= !insight || insight.stale
        if (insight?.inspect) {
          items.push({ ...base, id: JSON.stringify(['vegetation', field.id, season, field.boundary_revision,
            vegetation.geometry_fingerprint, insight.date]), kind: 'vegetation', date: insight.date,
          source: 'Sentinel-2', lowAreaPercent: insight.lowAreaPercent })
        }
      } catch (error) {
        if (signal.aborted) throw error
        incomplete = true
      }
      results[index] = { items, incomplete }
    }
  }
  await Promise.all(Array.from({ length: Math.min(3, fields.length) }, worker))
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  const items = results.flatMap((result) => result.items).sort((left, right) =>
    (left.kind === right.kind ? 0 : left.kind === 'water' ? -1 : 1)
    || (left.kind === 'water' ? left.date.localeCompare(right.date) : right.date.localeCompare(left.date))
    || left.fieldId.localeCompare(right.fieldId))
  return { items, unavailableFields: results.filter((result) => result.incomplete).length }
}
