import { request } from './client'
import type { HandoffSeasonalWater, HandoffSeasonalWaterRow } from './rotationHandoff'
export type WeatherSource = { provider: string; url: string; retrieved_at: string; description: string }
export type WeatherScope = { field_id: string; boundary_revision: number; geometry_fingerprint: string; generated_at: string; latitude: number; longitude: number; sources: WeatherSource[] }
export type FieldWeather = WeatherScope & {
  timezone: string; hours_complete: boolean;
  hours: { time: string; temperature_c: number | null; rain_mm: number | null; gust_ms: number | null; below_zero: boolean | null }[];
  forecast_days: { date: string; rain_mm: number | null; rain_probability_percent: number | null }[];
  requested_start: string; requested_end: string; missing_dates: string[];
  forecast_rain_mm: number | null; wettest_dates: string[]; lowest_probability_dates: string[];
}
export type RainPeriod = { length_days: number; dry_days: number | null; start: string; end: string; complete: boolean; missing_dates: string[]; rain_mm: number | null; typical_rain_mm: number | null; difference_mm: number | null; typical_status: 'not_requested' | 'available' | 'unavailable'; typical_periods: {start: string; end: string; rain_mm: number}[] }
export type RainHistory = WeatherScope & { timezone: 'UTC'; requested_end: string; available_end: string | null; archive_lag_days: number | null; periods: RainPeriod[]; limitations: string[] }
export type DashboardMetric = 'precipitation' | 'temperature_min' | 'temperature_max' | 'reference_et0' | 'surface_moisture'
export type DashboardSource = 'reanalysis' | 'forecast' | 'climate'
export type DashboardGap = 'archive_lag' | 'outside_coverage' | 'unsupported' | 'provider_gap'
export type DashboardPoint = { start: string; end: string; value: number }
export type DashboardSpan = {
  start: string; end: string; source: DashboardSource | null; reason: DashboardGap | null; points: DashboardPoint[]
}
export type WeatherDashboard = {
  field_id: string; boundary_revision: number; start: string; end: string; generated_at: string
  series: Record<DashboardMetric, DashboardSpan[]>
}
export type SeasonalWaterRow = HandoffSeasonalWaterRow
export type SeasonalWater = HandoffSeasonalWater
const path = (id: string) => '/fields/' + encodeURIComponent(id)
export const fieldWeatherApi = {
  dashboard: (id: string, revision: number, start: string, end: string, signal: AbortSignal) =>
    request<WeatherDashboard>(path(id) + '/weather-dashboard?' + new URLSearchParams({
      geometry_revision: String(revision), start, end,
    }), { signal }),
  forecast: (id: string, signal?: AbortSignal) => request<FieldWeather>(path(id) + '/weather-summary', { signal }),
  history: (id: string, typical: boolean, signal?: AbortSignal) => request<RainHistory>(path(id) + '/rainfall-history?typical=' + typical, { signal }),
  seasonal: (id: string, signal?: AbortSignal) => request<SeasonalWater>('/rotations/handoff/fields/' + encodeURIComponent(id) + '/seasonal-water?limit=20', { signal }),
}
