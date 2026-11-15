import type { Field, WaterForecast } from '../../api/contracts.ts'
import type { FieldProfileSnapshot } from '../../api/planningV1.generated.ts'
import type { VegetationResult } from '../../api/analytics.ts'
import { usableObservations } from '../fields/vegetationInsights.ts'
import { getCurrentWaterOutlook } from '../water/waterOutlook.ts'

/** A saved profile describes its own timestamp, never an issued historical forecast. */
export function profileForField(profiles: FieldProfileSnapshot[] | undefined, field: Field) {
  const profile = profiles?.find(p => p.field_id === field.id)
  if (!profile) return { status: 'missing' as const }
  if (profile.geometry_revision !== field.boundary_revision) return { status: 'boundary_changed' as const }
  return { status: 'available' as const, profile }
}

export function historyBeforeSeason(profile: FieldProfileSnapshot | undefined, season: number) {
  return [...(profile?.history ?? [])].filter(row => row.season < season).sort((a, b) => b.season - a.season)
}

export function rotationEvidence(profile: FieldProfileSnapshot | undefined, season: number) {
  const history = historyBeforeSeason(profile, season)
  const known = history.filter(row => row.status !== 'unknown' && row.crop)
  // A gap is not evidence of continuous cropping. Reported and confirmed remain distinguishable.
  const repeated = known.filter((row, i) => known[i + 1]?.season === row.season - 1
    && known[i + 1].crop?.crop_id.toLowerCase() === row.crop?.crop_id.toLowerCase())
  return { history, known, repeated, unknown: history.filter(row => row.status === 'unknown' || !row.crop) }
}

export function vegetationEvidence(result: VegetationResult | undefined, field: Field, season: number, today: string) {
  if (!result || result.field_id !== field.id || result.boundary_revision !== field.boundary_revision || result.season !== season) return undefined
  const observations = usableObservations(result).filter(o => o.date <= today && new Date(o.date).toISOString().slice(0, 10) === o.date)
  const latest = observations.at(-1)
  return latest ? { latest, count: observations.length,
    ageDays: Math.floor((Date.parse(today) - Date.parse(latest.date)) / 86_400_000),
    historical: season < Number(today.slice(0, 4)), assumedBoundary: result.boundary_history_assumed === true } : undefined
}

export type Insight = { section: 'water' | 'soil' | 'vegetation' | 'rotation'; code: string; priority: number }
export function overviewInsights(field: Field, season: number, today: string,
  forecast: WaterForecast | null | undefined, vegetation: VegetationResult | undefined, profile: FieldProfileSnapshot | undefined) {
  const water = getCurrentWaterOutlook(forecast, field.id, today, season)
  const satellite = vegetationEvidence(vegetation, field, season, today)
  const rotation = rotationEvidence(profile, season)
  const items: Insight[] = [
    { section: 'water', code: water.kind === 'shortage' ? 'waterShortage' : water.kind === 'clear' ? 'waterClear' : 'waterMissing', priority: water.kind === 'shortage' ? 0 : water.kind === 'clear' ? 6 : 3 },
    { section: 'soil', code: !profile ? 'profileMissing' : profile.land_status !== 'arable' ? 'landUnknown'
      : !profile.soil.some(h => h.quality === 'valid' && h.field_capacity_m3_m3 != null && h.wilting_point_m3_m3 != null) ? 'soilMissing' : 'soilSaved', priority: !profile || profile.land_status !== 'arable' ? 1 : 4 },
    { section: 'vegetation', code: !satellite ? 'vegetationMissing' : satellite.historical ? 'vegetationHistorical'
      : satellite.ageDays > 30 ? 'vegetationOld' : 'vegetationSaved', priority: !satellite ? 3 : 5 },
    { section: 'rotation', code: !rotation.known.length ? 'historyMissing' : rotation.repeated.length ? 'historyRepeated' : 'historySaved', priority: rotation.repeated.length ? 2 : 5 },
  ]
  return items.sort((a, b) => a.priority - b.priority)
}
