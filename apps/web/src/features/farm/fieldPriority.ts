import type { WaterForecast } from '../../api/contracts'
import { getCurrentWaterOutlook } from '../water/waterOutlook.ts'

export type FieldWaterPriority =
  | { kind: 'unavailable'; fieldId: string; reason: 'missing' | 'expired' | 'invalid' | 'stale' | 'incomplete' | 'request_failed' }
  | { kind: 'shortage' | 'clear'; fieldId: string; minimumMm: number; thresholdMm: number; marginMm: number;
    firstStressDate: string | null; daysToStress: number | null; startDate: string; endDate: string;
    initializedAt: string; values: number[] }

export function fieldWaterPriority(
  forecast: WaterForecast | null | undefined, fieldId: string, today: string,
): FieldWaterPriority {
  const unavailable = (reason: Extract<FieldWaterPriority, { kind: 'unavailable' }>['reason']): FieldWaterPriority =>
    ({ kind: 'unavailable', fieldId, reason })
  if (forecast && (forecast.analysis !== 'water_forecast' ||
      ('demo' in forecast && forecast.demo === true))) return unavailable('invalid')
  const outlook = getCurrentWaterOutlook(forecast, fieldId, today)
  if (!forecast || outlook.kind === 'unavailable') return unavailable(outlook.kind === 'unavailable' ? outlook.reason : 'missing')
  const initializedAt = Date.parse(forecast.initialized_at)
  const todayTime = Date.parse(today)
  if (initializedAt >= todayTime + 86_400_000) return unavailable('invalid')
  if (todayTime - initializedAt > 3 * 86_400_000) return unavailable('stale')
  if (outlook.firstDay.date !== today) return unavailable('incomplete')
  const firstStressDate = outlook.kind === 'shortage' ? outlook.firstShortage.date : null
  return { kind: outlook.kind, fieldId, minimumMm: outlook.minimum.reserve_mm,
    thresholdMm: outlook.thresholdMm, marginMm: outlook.minimum.reserve_mm - outlook.thresholdMm,
    firstStressDate, daysToStress: firstStressDate === null ? null : (Date.parse(firstStressDate) - todayTime) / 86_400_000,
    startDate: outlook.firstDay.date, endDate: outlook.lastDay.date, initializedAt: forecast.initialized_at,
    values: outlook.days.map((day) => day.reserve_mm) }
}

export function rankFieldWaterPriorities<T extends { priority: FieldWaterPriority }>(entries: readonly T[]): T[] {
  const rank = { shortage: 0, unavailable: 1, clear: 2 }
  return [...entries].sort((a, b) => {
    const left = a.priority, right = b.priority
    const status = rank[left.kind] - rank[right.kind]
    if (status) return status
    if (left.kind === 'unavailable' || right.kind === 'unavailable') return left.fieldId.localeCompare(right.fieldId)
    const days = (left.daysToStress ?? Infinity) - (right.daysToStress ?? Infinity)
    if (Number.isFinite(days) && days !== 0) return days
    const margin = left.marginMm / Math.max(1, left.thresholdMm) - right.marginMm / Math.max(1, right.thresholdMm)
    return margin || left.fieldId.localeCompare(right.fieldId)
  })
}
