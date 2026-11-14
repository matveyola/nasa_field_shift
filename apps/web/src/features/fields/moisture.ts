import type { Job, MoistureLayer, MoistureSource } from '../../api/contracts'

const millisecondsPerDay = 24 * 60 * 60 * 1000

export function latestMoistureArchiveDate(now = new Date(), source: MoistureSource = 'ml'): string {
  const lagDays = source === 'historical' ? 5 : 6
  return new Date(now.getTime() - lagDays * millisecondsPerDay).toISOString().slice(0, 10)
}

export function isStaleMoisture(date: string, now = new Date()): boolean {
  return Math.floor(now.getTime() / millisecondsPerDay) - Date.parse(date) / millisecondsPerDay > 9
}

function matchesLayer(layer: MoistureLayer | null | undefined, fieldId: string, season: number | undefined, source: MoistureSource): layer is MoistureLayer {
  if (!layer || layer.field_id !== fieldId || (season !== undefined && layer.season !== season)) return false
  if (source === 'demo') return layer.demo
  return !layer.demo && layer.source?.kind === (source === 'ml' ? 'ml' : 'reanalysis')
}

export function visibleMoisture(
  cached: MoistureLayer | null | undefined, job: Job | undefined,
  fieldId: string, season: number | undefined, source: MoistureSource, sampleDate: string,
): MoistureLayer | undefined {
  const result = job?.kind === 'geo_ml' && job.status === 'succeeded' && job.result && 'cells' in job.result
    ? job.result : undefined
  const latest = matchesLayer(cached, fieldId, season, source) && (!sampleDate || cached.date === sampleDate)
    ? cached : undefined
  if (!matchesLayer(result, fieldId, season, source)) return latest
  if (sampleDate && result.date !== sampleDate) return latest
  if (latest && latest.date > result.date) return latest
  if (latest?.source?.kind === 'ml' && result.source?.kind === 'ml' && (
    latest.date === result.date && latest.source.generated_at >= result.source.generated_at
  )) return latest
  return result
}
