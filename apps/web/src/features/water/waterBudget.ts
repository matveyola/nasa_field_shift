import type { WaterDay } from '../../api/contracts'

export type BudgetPoint = {
  date: string
  rain: number
  irrigation: number
  use: number
  drainage: number
  incoming: number
  outgoing: number
  change: number
  reserve: number
}

export type WaterBudget =
  | { kind: 'unavailable'; reason: 'empty' | 'invalid' | 'gap' | 'inconsistent' }
  | { kind: 'available'; opening: number; closing: number; points: BudgetPoint[]; totals: BudgetPoint }

const dayMs = 86_400_000
// The physical model serializes fluxes to four decimals. Permit rounding, not missing water.
const toleranceMm = 0.005

export function cumulativeWaterBudget(days: readonly WaterDay[]): WaterBudget {
  if (!days.length) return { kind: 'unavailable', reason: 'empty' }
  const ordered = [...days].sort((a, b) => a.date.localeCompare(b.date))
  const points: BudgetPoint[] = []
  let rain = 0, irrigation = 0, use = 0, drainage = 0
  for (const [index, day] of ordered.entries()) {
    const timestamp = Date.parse(day.date)
    if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== day.date ||
        [day.opening_mm, day.reserve_mm, day.rain_mm, day.irrigation_mm, day.water_use_mm, day.drainage_mm]
          .some((value) => typeof value !== 'number' || !Number.isFinite(value) || value < 0)) {
      return { kind: 'unavailable', reason: 'invalid' }
    }
    const previous = ordered[index - 1]
    if (previous && timestamp - Date.parse(previous.date) !== dayMs) {
      return { kind: 'unavailable', reason: 'gap' }
    }
    if ((previous && Math.abs(previous.reserve_mm - day.opening_mm) > toleranceMm) ||
        Math.abs(day.opening_mm + day.rain_mm + day.irrigation_mm - day.water_use_mm - day.drainage_mm - day.reserve_mm) > toleranceMm) {
      return { kind: 'unavailable', reason: 'inconsistent' }
    }
    rain += day.rain_mm
    irrigation += day.irrigation_mm
    use += day.water_use_mm
    drainage += day.drainage_mm
    const incoming = rain + irrigation
    const outgoing = use + drainage
    points.push({ date: day.date, rain, irrigation, use, drainage, incoming, outgoing,
      change: incoming - outgoing, reserve: day.reserve_mm })
  }
  return { kind: 'available', opening: ordered[0].opening_mm, closing: ordered.at(-1)!.reserve_mm,
    points, totals: points.at(-1)! }
}
