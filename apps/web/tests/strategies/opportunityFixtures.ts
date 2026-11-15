/** Synthetic calendar contract fixtures, never used as production data. */
import type { PlantingOpportunities, PlantingOpportunity } from '../../src/api/plantingOpportunities'
export function opportunity(overrides: Partial<PlantingOpportunity> = {}): PlantingOpportunity {
  return { id: 'winter-wheat', crop_id: 'Wheat', cycle_id: 'winter-1', system: 'rainfed', calendar_kind: 'regional_typical_month', date_precision: 'month', calendar_status: 'upcoming',
    planting_period: { start: '2027-09-01', end: '2027-09-30' }, maturity_period: { start: '2028-07-01', end: '2028-07-31' }, maturity_basis: 'regional_maturity_month', harvest_year: 2028, next_reference_date: '2027-09-01', eligibility: 'not_assessed', missing_data: ['field_weather', 'soil_profile'], reasons: ['Synthetic test regional reference; no field evaluation.'],
    source: { kind: 'regional_typical_month', version: 'synthetic-calendar-v1', source_url: 'https://example.org/test-calendar', license: 'synthetic-only', checksum_sha256: 'a'.repeat(64), available_at: '2026-01-01T00:00:00Z', reference_year: 2015, resolution_arc_minutes: 5, grid_latitude: 50.5, grid_longitude: 30.5 }, profit: null, profit_status: 'unassessed', allocation_supported: true, native_crop_profile_available: true, model_evaluation_status: 'not_run', ...overrides }
}
export function opportunities(fieldId = 'north', planningDate = '2027-06-15', yearCount = 1): PlantingOpportunities {
  return { field_id: fieldId, geometry_revision: 1, profile_revision: null, profile_status: 'not_found', planning_date: planningDate, generated_at: '2026-11-15T00:00:00Z', knowledge_as_of: '2026-11-15T00:00:00Z', year_count: yearCount, lookahead_end: `${Number(planningDate.slice(0, 4)) + yearCount}${planningDate.slice(4)}`, horizon_basis: 'rolling_calendar_years', allocation_horizon_max_years: 6, model_request_horizon_max_years: 3, ranking: 'calendar_chronology_not_profit', limitations: ['Synthetic reference calendar: weather and profit not assessed.'], items: [
    opportunity(),
    opportunity({ id: 'reviewed', crop_id: 'Soy', cycle_id: 'reviewed-1', calendar_kind: 'reviewed_window', date_precision: 'day', calendar_status: 'open', planting_period: { start: '2027-06-10', end: '2027-06-20' }, maturity_period: { start: '2027-09-10', end: '2027-09-20' }, harvest_year: 2027, eligibility: 'review_required', maturity_basis: 'reviewed_growth_window_end', source: { ...opportunity().source!, kind: 'reviewed_window' } }),
    opportunity({ id: 'current', crop_id: 'Maize', calendar_status: 'current_reference_month', planting_period: { start: '2027-06-01', end: '2027-06-30' } }),
    opportunity({ id: 'passed', crop_id: 'Barley', calendar_status: 'passed', planting_period: { start: '2027-03-01', end: '2027-03-31' } }),
    opportunity({ id: 'peas', crop_id: 'Peas', cycle_id: null, calendar_kind: 'unavailable', calendar_status: 'unknown', date_precision: 'unknown', source: null, planting_period: null, maturity_period: null, maturity_basis: 'unknown', harvest_year: null, eligibility: 'unavailable' }),
    opportunity({ id: 'rice', crop_id: 'Rice', allocation_supported: false, native_crop_profile_available: false }),
  ] }
}
