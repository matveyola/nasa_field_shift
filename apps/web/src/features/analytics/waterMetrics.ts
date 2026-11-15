import type { WaterDay } from '../../api/contracts'
import type { SeasonalWaterRow } from '../../api/fieldWeather'

const dayMs = 86_400_000
export function waterMetrics(days: WaterDay[], threshold: number, flowering?: { start: string; end: string }) {
  const ordered = [...days].sort((a, b) => a.date.localeCompare(b.date))
  if (!Number.isFinite(threshold) || threshold < 0 || ordered.some((d, i) => !Number.isFinite(d.reserve_mm)
    || !Number.isFinite(Date.parse(d.date)) || (i > 0 && ordered[i - 1].date === d.date))) return null
  let longest = 0; let length = 0; let start: string | null = null
  let longestStart: string | null = null; let longestEnd: string | null = null
  for (let i = 0; i < ordered.length; i++) {
    const d = ordered[i]
    const contiguous = i > 0 && Date.parse(d.date) - Date.parse(ordered[i - 1].date) === dayMs
    if (!contiguous || d.reserve_mm >= threshold) { length = 0; start = null }
    if (d.reserve_mm < threshold) {
      if (length === 0) start = d.date
      length++
      if (length > longest) { longest = length; longestStart = start; longestEnd = d.date }
    }
  }
  const sum = (key: 'rain_mm' | 'irrigation_mm' | 'water_use_mm' | 'drainage_mm') => ordered.length && ordered.every(d => Number.isFinite(d[key])) ? ordered.reduce((total, d) => total + d[key], 0) : null
  const below = ordered.filter(d => d.reserve_mm < threshold)
  const minimum = ordered.reduce<WaterDay | null>((lowest, d) => !lowest || d.reserve_mm < lowest.reserve_mm ? d : lowest, null)
  return { firstBelow: below[0]?.date ?? null, minimum, stressDays: below.length,
    longest, longestStart, longestEnd, rain: sum('rain_mm'), irrigation: sum('irrigation_mm'), use: sum('water_use_mm'), drainage: sum('drainage_mm'),
    floweringStressDays: flowering ? below.filter(d => d.date >= flowering.start && d.date <= flowering.end).length : null }
}
export function range(values: (number | null | undefined)[]) {
  const sorted = values.filter((n): n is number => n != null && Number.isFinite(n)).sort((a,b) => a-b)
  if (!sorted.length) return null
  const mid = Math.floor(sorted.length / 2)
  return { median: sorted.length % 2 ? sorted[mid] : (sorted[mid-1] + sorted[mid]) / 2, min: sorted[0], max: sorted.at(-1)!, count: sorted.length }
}
export function seasonalGroups(rows: SeasonalWaterRow[]) {
  const groups = new Map<string, SeasonalWaterRow[]>()
  for (const row of rows) {
    const key = JSON.stringify([row.plan_id, row.crop_id, row.start_date, row.end_date, row.data_kind])
    // Exact job/scenario duplicates cannot gain extra weight in the displayed median.
    const group = groups.get(key) ?? []
    if (!group.some(r => r.job_id === row.job_id && r.scenario_id === row.scenario_id)) group.push(row)
    groups.set(key, group)
  }
  return [...groups.entries()].map(([key, runs]) => ({ key, first: runs[0], runs,
    deficitPercent: range(runs.map(r => r.demand_mm != null && r.demand_mm > 0 && r.deficit_mm != null ? 100 * r.deficit_mm / r.demand_mm : null)),
    droughtPercent: range(runs.map(r => r.crop_days != null && r.crop_days > 0 && r.drought_days != null ? 100 * r.drought_days / r.crop_days : null)),
  }))
}
