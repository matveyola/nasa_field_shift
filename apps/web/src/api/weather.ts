import { request } from './client'

export type SeasonalWeatherDay = {
  date: string
  temperature_min_c: number | null
  temperature_max_c: number | null
  precipitation_mm: number | null
}

type FieldWeather = {
  field_id: string
  boundary_revision: number
  start_date: string
  end_date: string
  grid_latitude: number
  grid_longitude: number
  timezone: string
  source: string
  source_url: string
  days: SeasonalWeatherDay[]
}

export type SeasonalWeather = FieldWeather & { season: number; resolution_degrees: number }
export type WeatherForecast = FieldWeather & { fetched_at: string }

export function getSeasonalWeather(fieldId: string, season: number, signal: AbortSignal) {
  return request<SeasonalWeather | null>(
    '/fields/' + encodeURIComponent(fieldId) + '/seasonal-weather?' + new URLSearchParams({ season: String(season) }),
    { signal },
  )
}

export function getWeatherForecast(fieldId: string, signal: AbortSignal) {
  return request<WeatherForecast>('/fields/' + encodeURIComponent(fieldId) + '/weather-forecast', { signal })
}
