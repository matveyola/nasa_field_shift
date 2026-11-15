import type { Field, WaterDay, WaterForecast } from '../../api/contracts.ts'
import type { DashboardSpan } from '../../api/fieldWeather.ts'
import { currentWater } from './currentWater.ts'

export type Period = { start: string; end: string }
type DroughtGap = Extract<ReturnType<typeof currentWater>['outlook'], { kind: 'unavailable' }>['reason'] | 'historical'
const dayMs = 86_400_000

export function shiftDay(day: string, amount: number) {
  return new Date(Date.parse(day) + amount * dayMs).toISOString().slice(0, 10)
}

export function isDay(day: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) && Number.isFinite(Date.parse(day)) && shiftDay(day, 0) === day
}

export function periodDays(period: Period) {
  return (Date.parse(period.end) - Date.parse(period.start)) / dayMs + 1
}

export function contains(period: Period, day: string) {
  return day >= period.start && day <= period.end
}

export function droughtWindow(field: Field, forecast: WaterForecast | null | undefined, today: string, selected: Period) {
  const live = selected.start === today
  const period = live ? { start: today, end: shiftDay(today, 29) } : selected
  const { outlook, current, days } = currentWater(field, forecast, today)
  const covered = days.filter(({ date }) => contains(period, date))
  const risks: Period[] = []
  for (const day of covered) {
    if (!current || day.reserve_mm >= current.threshold_mm) continue
    const last = risks.at(-1)
    if (last && shiftDay(last.end, 1) === day.date) last.end = day.date
    else risks.push({ start: day.date, end: day.date })
  }
  const complete = covered.length === periodDays(period)
  const applicable = current?.applicability?.status === 'not_assessed' &&
    covered.every(({ date }) => current.applicability?.soil_temperature.some(
      sample => sample.date === date && Number.isFinite(sample.mean_c) && sample.mean_c > 0,
    ))
  const reason: DroughtGap = period.end < today ? 'historical' : outlook.kind === 'unavailable' ? outlook.reason : 'incomplete'
  return { live, period, covered, current, complete, applicable, risks, reason }
}

export function weatherTotals(spans: DashboardSpan[]) {
  return spans.flatMap(span => span.source && span.points.length ? [{
    ...span, source: span.source,
    total: span.points.reduce((sum, point) => sum + point.value * periodDays(point), 0),
  }] : [])
}

export function reservePoints(days: WaterDay[]) {
  const spans: DashboardSpan[] = []
  for (const day of days) {
    const point = { start: day.date, end: day.date, value: day.reserve_mm }
    const last = spans.at(-1)
    if (last && shiftDay(last.end, 1) === day.date) {
      last.end = day.date
      last.points.push(point)
    } else spans.push({ ...point, source: 'forecast', reason: null, points: [point] })
  }
  return spans
}
