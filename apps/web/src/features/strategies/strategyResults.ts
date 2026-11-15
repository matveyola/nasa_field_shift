import type { HandoffResult, StrategyCandidate, StrategyContext, StrategyPriority } from '../../api/rotationHandoff'

export const priorityMetrics = {
  water_stress: ['water', 'water_stress_days_pct', '%', 'Water stress', 'Водний стрес'],
  water_deficit: ['water', 'water_deficit_pct', '%', 'Water deficit', 'Дефіцит води'],
  water_saving: ['water', 'scheduled_irrigation_mm', 'mm', 'Irrigation / water savings', 'Полив / економія води'],
  yield: ['economics', 'marketable_yield_t_ha', 't/ha', 'Reference yield', 'Довідкова врожайність'],
  revenue: ['economics', 'gross_revenue_per_ha', 'money', 'Gross revenue', 'Виручка'],
  revenue_after_seed: ['economics', 'revenue_after_seed_per_ha', 'money', 'Revenue after seed cost', 'Дохід без ціни культури'],
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

export function strategyResult(result: HandoffResult) {
  const candidates = candidateRows(result.payload.candidates)
  const ranking = result.payload.ranking as { status?: string; selection_ids?: unknown } | undefined
  const ranked = result.ranking_usable && ranking?.status === 'ranked'
  const eligible = candidates.filter(row => row.status === 'ok')
  const rows = !ranked && Array.isArray(ranking?.selection_ids)
    ? ranking.selection_ids.flatMap(id => eligible.filter(row => (row.choice_id ?? row.crop_id) === id))
    : candidateRows(result.payload.top_candidates).filter(row => row.status === 'ok')
  const unique = new Map<string, StrategyCandidate>()
  for (const row of rows) if (!unique.has(row.crop_id)) unique.set(row.crop_id, row)
  return { candidates, picks: [...unique.values()].slice(0, 10), ranked }
}

const isoDay = (year: number, month: number, day: number) => `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`

export function plantingBounds(context: StrategyContext, count: number, previousDate?: string, firstDate?: string) {
  const firstYear = previousDate ? Number(previousDate.slice(0, 4)) + 1 : context.min_year
  const scenarioLastYear = firstDate ? Number(firstDate.slice(0, 4)) + context.max_calendar_years - 1 : context.max_year
  const lastYear = Math.min(context.max_year, scenarioLastYear) - count + 1
  const lastDay = new Date(Date.UTC(lastYear, context.max_month, 0)).getUTCDate()
  return { min: isoDay(firstYear, context.min_month, 1), max: isoDay(lastYear, context.max_month, lastDay) }
}

export function validPlantingDate(value: string, context: StrategyContext, count: number, previousDate?: string, firstDate?: string) {
  const { min, max } = plantingBounds(context, count, previousDate, firstDate)
  const month = Number(value.slice(5, 7))
  const parsed = new Date(value)
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value && value >= min && value <= max &&
    month >= context.min_month && month <= context.max_month
}

export function nextPlantingDate(context: StrategyContext, count: number, previousDate?: string, today = new Date().toISOString().slice(0, 10), firstDate?: string) {
  const { min, max } = plantingBounds(context, count, previousDate, firstDate)
  if (!previousDate && validPlantingDate(today, context, count)) return today
  const year = previousDate ? Number(min.slice(0, 4)) : Math.min(Number(max.slice(0, 4)), Math.max(Number(min.slice(0, 4)),
    Number(today.slice(0, 4)) + Number(Number(today.slice(5, 7)) > context.max_month)))
  const preferred = isoDay(year, Math.min(Math.max(5, context.min_month), context.max_month), 1)
  return validPlantingDate(preferred, context, count, previousDate, firstDate) ? preferred : min
}
