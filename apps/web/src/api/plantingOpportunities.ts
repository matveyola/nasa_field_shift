import { request } from './client'

export type CalendarPeriod = { start: string; end: string }
export type PlantingCalendarSource = {
  kind: 'reviewed_window' | 'regional_typical_month'
  version: string; source_url: string; license: string | null; checksum_sha256: string | null
  available_at: string; reference_year: number | null; resolution_arc_minutes: number | null
  grid_latitude: number | null; grid_longitude: number | null
}
export type PlantingOpportunity = {
  id: string; crop_id: string; cycle_id: string | null; system: string
  calendar_kind: 'reviewed_window' | 'regional_typical_month' | 'unavailable'
  date_precision: 'day' | 'month' | 'unknown'
  calendar_status: 'open' | 'current_reference_month' | 'upcoming' | 'passed' | 'unknown'
  planting_period: CalendarPeriod | null; maturity_period: CalendarPeriod | null
  maturity_basis: 'reviewed_growth_window_end' | 'regional_maturity_month' | 'unknown'
  harvest_year: number | null; next_reference_date: string | null
  eligibility: 'review_required' | 'not_assessed' | 'ineligible' | 'unavailable'
  missing_data: string[]; reasons: string[]; source: PlantingCalendarSource | null
  profit: null; profit_status: 'unassessed'; allocation_supported: boolean
  native_crop_profile_available: boolean; model_evaluation_status: 'not_run'
}
export type PlantingOpportunities = {
  field_id: string; geometry_revision: number; profile_revision: number | null; profile_status: string
  planning_date: string; generated_at: string; knowledge_as_of: string
  year_count: number; lookahead_end: string; horizon_basis: 'rolling_calendar_years'
  allocation_horizon_max_years: 6; model_request_horizon_max_years: 3
  ranking: 'calendar_chronology_not_profit'; items: PlantingOpportunity[]; limitations: string[]
}

export const plantingOpportunitiesApi = {
  get: (fieldId: string, planningDate: string, yearCount = 1, signal?: AbortSignal) => {
    const query = new URLSearchParams({ planning_date: planningDate, year_count: String(yearCount) })
    return request<PlantingOpportunities>('/fields/' + encodeURIComponent(fieldId) + '/planting-opportunities?' + query, { signal })
  },
}
