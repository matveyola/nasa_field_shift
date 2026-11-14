import type { Job, WaterForecast } from '../../api/contracts'

export function selectWaterForecast(fieldId: string, job?: Job, saved?: WaterForecast | null) {
  const stored = saved?.field_id === fieldId ? saved : undefined
  const active = job?.result
  if (job?.kind !== 'simulation' || job.status !== 'succeeded' || !active
    || !('analysis' in active) || active.analysis !== 'water_forecast' || active.field_id !== fieldId) return stored
  if (!stored) return active

  const issueDifference = Date.parse(active.initialized_at) - Date.parse(stored.initialized_at)
  if (issueDifference > 0 || (issueDifference === 0 && Date.parse(active.generated_at) > Date.parse(stored.generated_at))) {
    return active
  }
  return stored
}
