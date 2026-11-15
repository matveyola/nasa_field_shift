import type { HandoffResult, StrategyCandidate, StrategyContext, StrategyPriority } from '../../api/rotationHandoff'

export const priorityMetrics = {
  water_stress: ['water', 'water_stress_days_pct', '%', 'Water stress', 'Водний стрес'],
  water_deficit: ['water', 'water_deficit_pct', '%', 'Water deficit', 'Дефіцит води'],
  water_saving: ['water', 'scheduled_irrigation_mm', 'mm', 'Irrigation / water savings', 'Полив / економія води'],
  yield: ['economics', 'marketable_yield_t_ha', 't/ha', 'Reference yield', 'Довідкова врожайність'],
  revenue: ['economics', 'gross_revenue_per_ha', 'money', 'Gross revenue', 'Виручка'],
  revenue_after_seed: ['economics', 'revenue_after_seed_per_ha', 'money', 'Revenue after seed cost', 'Виручка після вартості насіння'],
  soil_cover: ['water', 'living_cover_days', 'days', 'Living soil cover', 'Живий покрив ґрунту'],
} as const satisfies Record<StrategyPriority, readonly string[]>

export function metric(candidate: StrategyCandidate, key: StrategyPriority) {
  const [group, name, unit] = priorityMetrics[key]
  const amount = candidate[group]?.[name]
  const currency = candidate.economics?.currency
  if (typeof amount !== 'number' || !Number.isFinite(amount)) return null
  if (unit === 'money') return typeof currency === 'string' && /^[A-Z]{3}$/.test(currency)
    ? { amount, unit: `${currency}/ha` } : null
  return { amount, unit }
}

export function metricDifference(first: StrategyCandidate, second: StrategyCandidate, key: StrategyPriority) {
  const a = metric(first, key)
  const b = metric(second, key)
  return a && b && a.unit === b.unit ? { amount: b.amount - a.amount, unit: a.unit === '%' ? 'pp' : a.unit } : null
}

function candidateRows(value: unknown): StrategyCandidate[] {
  if (!Array.isArray(value)) return []
  return value.filter((row): row is StrategyCandidate => typeof row?.crop_id === 'string' &&
    ['ok', 'unsupported', 'unavailable'].includes(row.status))
}

export const strategyChoiceId = (row: StrategyCandidate) => row.choice_id ?? row.crop_id

export function strategyResult(result: HandoffResult) {
  const candidates = candidateRows(result.payload.candidates)
  const ranking = result.payload.ranking as { status?: string; selection_ids?: unknown } | undefined
  const ranked = result.ranking_usable && ranking?.status === 'ranked'
  const eligible = candidates.filter(row => row.status === 'ok')
  const rows = !ranked && Array.isArray(ranking?.selection_ids)
    ? ranking.selection_ids.flatMap(id => eligible.filter(row => strategyChoiceId(row) === id))
    : candidateRows(result.payload.top_candidates).filter(row => row.status === 'ok')
  const unique = new Map<string, StrategyCandidate>()
  for (const row of rows) if (!unique.has(strategyChoiceId(row))) unique.set(strategyChoiceId(row), row)
  return { candidates, picks: [...unique.values()], ranked }
}

const isoDay = (year: number, month: number, day: number) => `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
const yearOf = (day: string) => Number(day.slice(0, 4))
const monthOf = (day: string) => Number(day.slice(5, 7))

function nextDay(day: string) {
  const date = new Date(day)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
}

function firstSupportedDay(context: StrategyContext, day: string) {
  const month = monthOf(day)
  if (month < context.min_month) return isoDay(yearOf(day), context.min_month, 1)
  if (month > context.max_month) return isoDay(yearOf(day) + 1, context.min_month, 1)
  return day
}

export function plantingBounds(context: StrategyContext, previousEnd?: string, firstDate?: string) {
  const min = previousEnd ? firstSupportedDay(context, nextDay(previousEnd)) : isoDay(context.min_year, context.min_month, 1)
  const scenarioLastYear = firstDate ? yearOf(firstDate) + context.max_calendar_years - 1 : context.max_year
  const lastYear = Math.min(context.max_year, scenarioLastYear)
  const lastDay = new Date(Date.UTC(lastYear, context.max_month, 0)).getUTCDate()
  return { min, max: isoDay(lastYear, context.max_month, lastDay) }
}

export function validPlantingDate(value: string, context: StrategyContext, previousEnd?: string, firstDate?: string) {
  const { min, max } = plantingBounds(context, previousEnd, firstDate)
  const month = monthOf(value)
  const parsed = new Date(value)
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value && value >= min && value <= max &&
    month >= context.min_month && month <= context.max_month
}

export function initialPlantingDate(context: StrategyContext, today = new Date().toISOString().slice(0, 10)) {
  const { min, max } = plantingBounds(context)
  if (validPlantingDate(today, context)) return today
  const year = Math.min(yearOf(max), Math.max(yearOf(min), yearOf(today) + Number(monthOf(today) > context.max_month)))
  const preferred = isoDay(year, Math.min(Math.max(5, context.min_month), context.max_month), 1)
  return validPlantingDate(preferred, context) ? preferred : min
}

export function afterCrop(context: StrategyContext, previous: StrategyCandidate, previousSowing: string, firstDate: string) {
  const scenarios = Object.values(previous.scenarios ?? {})
  const latest = (dates: string[]) => dates.sort().at(-1)
  const previousEnd = latest(scenarios.flatMap(row => row.end_date ? [row.end_date] : [])) ?? previousSowing
  const recoveryDates = scenarios.flatMap(({ end_date, post_crop_recharge: recovery }) =>
    end_date && recovery?.status === 'reached' && recovery.target_reached_on && recovery.target_reached_on >= end_date
      ? [recovery.target_reached_on] : [])
  const recoveredOn = recoveryDates.length === scenarios.length ? latest(recoveryDates) : undefined
  const dayAfterRecovery = recoveredOn ? nextDay(recoveredOn) : undefined
  const proposed = dayAfterRecovery ? firstSupportedDay(context, dayAfterRecovery) : undefined
  const suggested = proposed && validPlantingDate(proposed, context, previousEnd, firstDate) ? proposed : undefined
  const searchedThrough = latest(scenarios.flatMap(row => row.post_crop_recharge?.searched_through ? [row.post_crop_recharge.searched_through] : []))
  return { previousEnd, recoveredOn, suggested, dayAfterRecovery, searchedThrough }
}
