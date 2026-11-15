import type { HandoffResult } from '../../../api/rotationHandoff'
import { array, object, rankedRecommendations, string, strings } from './model.ts'

export type WeatherBasis = 'historical' | 'live' | 'mixed' | 'unknown'
export type BriefCheck = 'unavailable' | 'land' | 'thermal' | 'review' | 'resources' | 'priority' | 'currencies' | 'costs' | 'benchmark' | 'weather' | 'timing'

function day(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const time = Date.parse(`${value}T00:00:00Z`)
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null
}

/** A requested live segment or an envelope name does not prove live weather was used. */
export function weatherBasis(result: HandoffResult): WeatherBasis {
  const sources = new Set<string>()
  const plans = array(result.payload.plans)
  if (!plans.length) return 'unknown'
  for (const plan of plans) {
    const members = array(object(plan).members)
    if (!members.length) return 'unknown'
    for (const member of members) {
      const seasons = array(object(member).seasons)
      if (!seasons.length) return 'unknown'
      for (const item of seasons) {
        const season = object(item), segments = array(season.weather_segments)
        const first = day(season.planting_date), last = day(season.through_date)
        if (!segments.length || first === null || last === null || first > last) return 'unknown'
        let next = first
        for (const item of segments) {
          const segment = object(item), dates = object(segment.dates)
          const start = day(dates.start), end = day(dates.end)
          if (start !== next || end === null || end < start || end > last) return 'unknown'
          if (segment.source !== 'historical_analog' && segment.source !== 'live_forecast') return 'unknown'
          if (segment.source === 'historical_analog') {
            const history = object(segment.historical_dates), a = day(history.start), b = day(history.end)
            if (a === null || b === null || b - a !== end - start) return 'unknown'
          }
          sources.add(segment.source)
          next = end + 86400000
        }
        if (next !== last + 86400000) return 'unknown'
      }
    }
  }
  return sources.size === 2 ? 'mixed' : sources.has('live_forecast') ? 'live' : 'historical'
}

export function canUseRanking(result: HandoffResult): boolean {
  return result.status === 'available' && result.operation === 'forecast' && result.ranking_usable === true
    && result.suitability_status === 'arable_land_only'
    && !array(result.diagnostics).some(row => object(row).severity === 'blocking')
}

/** Describe existing evidence. Never infer crop advantages, prices, ranks or probabilities. */
export function decisionBrief(result: HandoffResult) {
  const weather = weatherBasis(result), decision = object(result.payload.decision)
  const plans = array(result.payload.plans).map(object), rows = array(decision.decisions).map(object)
  const checks: BriefCheck[] = []
  if (result.status !== 'available') checks.push('unavailable')
  else {
    if (result.suitability_status === 'not_assessed') checks.push('land')
    else if (result.suitability_status === 'no_thermal_growth') checks.push('thermal')
    else if (result.suitability_status === 'review_required' || !canUseRanking(result) && result.ranking_usable) checks.push('review')
    if (!rows.length || rows.some(row => row.feasibility !== 'feasible' || strings(row.blockers).length || strings(row.unknowns).length)) checks.push('resources')
    if (strings(decision.priorities_missing).length) checks.push('priority')
    const economics = plans.map(plan => object(plan.economics))
    const currencies = new Set(economics.map(row => string(row.gross_revenue_currency)).filter(Boolean))
    if (currencies.size > 1) checks.push('currencies')
    if (economics.some(row => row.gross_revenue_per_ha != null && row.gross_margin_per_ha == null)) checks.push('costs')
    else if (economics.some(row => strings(row.input_origins).includes('published_benchmark'))) checks.push('benchmark')
    checks.push(weather === 'unknown' ? 'weather' : 'timing')
  }
  const ranked = rankedRecommendations(result.payload, canUseRanking(result))
  return { weather, checks: checks.slice(0, 2), ranked: ranked.map(row => string(row.id)!).filter(Boolean), canCompare: result.status === 'available' && result.operation === 'forecast' && plans.length > 0 }
}

export const weatherLabels: Record<WeatherBasis, [string, string]> = {
  historical: ['Historical weather scenarios for the selected dates', 'Історичні погодні сценарії для обраних дат'],
  live: ['Short forecast for the returned crop periods', 'Короткий прогноз для повернутих періодів культур'],
  mixed: ['Short forecast and historical weather scenarios', 'Короткий прогноз та історичні погодні сценарії'],
  unknown: ['Weather coverage is incomplete or unspecified in this result', 'Погодне покриття в цьому результаті неповне або не зазначене'],
}
