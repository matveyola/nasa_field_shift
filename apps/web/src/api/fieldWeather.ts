import { request } from './client'
export type WeatherSource = { provider: string; url: string; retrieved_at: string; description: string }
export type WeatherScope = { field_id: string; boundary_revision: number; geometry_fingerprint: string; generated_at: string; latitude: number; longitude: number; sources: WeatherSource[] }
export type FieldWeather = WeatherScope & {
  timezone: string; hours_complete: boolean;
  hours: { time: string; temperature_c: number | null; rain_mm: number | null; gust_ms: number | null; below_zero: boolean | null }[];
  forecast_days: { date: string; rain_mm: number | null; rain_probability_percent: number | null }[];
  requested_start: string; requested_end: string; missing_dates: string[];
  forecast_rain_mm: number | null; wettest_dates: string[]; lowest_probability_dates: string[];
}
export type RainPeriod = { length_days: number; start: string; end: string; complete: boolean; missing_dates: string[]; rain_mm: number | null; typical_rain_mm: number | null; difference_mm: number | null; typical_status: 'not_requested' | 'available' | 'unavailable'; typical_periods: {start: string; end: string; rain_mm: number}[] }
export type RainHistory = WeatherScope & { timezone: 'UTC'; requested_end: string; available_end: string | null; archive_lag_days: number | null; periods: RainPeriod[]; limitations: string[] }
export type SeasonalWaterRow = {
  job_id: string; scenario_id: string; plan_id: string; crop_id: string; start_date: string; end_date: string; data_kind: string; evidence_sha256: string;
  demand_mm: number | null; use_mm: number | null; deficit_mm: number | null; drought_days: number | null; crop_days: number | null;
  opening_mm: number | null; closing_mm: number | null; irrigation_mm: number | null; drainage_mm: number | null; waterlogging_days: number | null; water_stress_days: number | null;
}
export type SeasonalWater = { field_id: string; geometry_revision: number; rows: SeasonalWaterRow[] }
const path = (id: string) => '/fields/' + encodeURIComponent(id)
export const fieldWeatherApi = {
  forecast: (id: string, signal?: AbortSignal) => request<FieldWeather>(path(id) + '/weather-summary', { signal }),
  history: (id: string, typical: boolean, signal?: AbortSignal) => request<RainHistory>(path(id) + '/rainfall-history?typical=' + typical, { signal }),
  seasonal: (id: string, signal?: AbortSignal) => request<SeasonalWater>('/rotations/handoff/fields/' + encodeURIComponent(id) + '/seasonal-water?limit=20', { signal }),
}
