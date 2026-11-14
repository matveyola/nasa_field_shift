import type { WaterForecast } from '../../api/contracts'

type ForecastDay = WaterForecast['days'][number]
type ForecastInput = Pick<WaterForecast, 'field_id' | 'initialized_at' | 'threshold_mm' | 'days'>
type AvailableOutlook = {
  days: ForecastDay[]
  firstDay: ForecastDay
  lastDay: ForecastDay
  minimum: ForecastDay
  thresholdMm: number
}

type WaterOutlook =
  | { kind: 'unavailable'; reason: 'missing' | 'expired' | 'invalid' | 'stale' | 'incomplete' }
  | (AvailableOutlook & ({ kind: 'clear' } | { kind: 'shortage'; firstShortage: ForecastDay }))

export function getWaterOutlook(
  forecast: ForecastInput | null | undefined, fieldId: string, today: string,
): WaterOutlook {
  if (!forecast) return { kind: 'unavailable', reason: 'missing' }

  const { threshold_mm: thresholdMm } = forecast
  if (forecast.field_id !== fieldId || !isIsoDay(today) || !Number.isFinite(Date.parse(forecast.initialized_at)) ||
      !Number.isFinite(thresholdMm) || thresholdMm < 0 ||
      forecast.days.some(({ date }) => !isIsoDay(date))) {
    return { kind: 'unavailable', reason: 'invalid' }
  }

  const days = forecast.days.filter(({ date }) => date >= today).sort((a, b) => a.date.localeCompare(b.date))
  if (!days.length) return { kind: 'unavailable', reason: forecast.days.length ? 'expired' : 'missing' }
  if (days.some((day, index) => !Number.isFinite(day.reserve_mm) || day.reserve_mm < 0 ||
      (index > 0 && Date.parse(day.date) - Date.parse(days[index - 1].date) !== 86_400_000))) {
    return { kind: 'unavailable', reason: 'invalid' }
  }

  const minimum = days.reduce((lowest, day) => day.reserve_mm < lowest.reserve_mm ? day : lowest)
  // Use the physical reserve that the threshold is calibrated for, even when an ML estimate is present.
  const firstShortage = days.find(({ reserve_mm }) => reserve_mm < thresholdMm)
  const available = { days, firstDay: days[0], lastDay: days[days.length - 1], minimum, thresholdMm }
  return firstShortage ? { kind: 'shortage', ...available, firstShortage } : { kind: 'clear', ...available }
}

function isIsoDay(value: string) {
  const time = Date.parse(value)
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value
}

/** Live signals require today's coverage, a recent issue and the selected season. */
export function getCurrentWaterOutlook(forecast: WaterForecast | null | undefined, fieldId: string, today: string, season?: number): WaterOutlook {
  if (forecast && (forecast.analysis !== 'water_forecast' || ('demo' in forecast && forecast.demo === true)
    || forecast.season !== Number(today.slice(0, 4)) || (season !== undefined && forecast.season !== season))) {
    return { kind: 'unavailable', reason: 'invalid' }
  }
  const outlook = getWaterOutlook(forecast, fieldId, today)
  if (!forecast || outlook.kind === 'unavailable') return outlook
  const age = Date.parse(today) - Date.parse(forecast.initialized_at)
  if (age <= -86_400_000) return { kind: 'unavailable', reason: 'invalid' }
  if (age > 3 * 86_400_000) return { kind: 'unavailable', reason: 'stale' }
  if (outlook.firstDay.date !== today) return { kind: 'unavailable', reason: 'incomplete' }
  return outlook
}
