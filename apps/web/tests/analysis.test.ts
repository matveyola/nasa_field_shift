import assert from 'node:assert/strict'
import test from 'node:test'
import type { FieldContext, Job, WaterForecast, WaterResult } from '../src/api/contracts.ts'
import { selectWaterForecast } from '../src/features/water/waterForecast.ts'
import { matchingWaterResult } from '../src/shared/analysis.ts'

const context: FieldContext = { field_id: 'F-03', scenario: 'dry_year', season: 2026 }
const result: WaterResult = {
  ...context,
  demo: true,
  threshold_mm: 60,
  flowering_start: '2026-08-03',
  flowering_end: '2026-08-07',
  first_stress_date: '2026-08-03',
  min_reserve_mm: 58,
  stress_days: 7,
  days: [],
}
const job: Job = {
  id: 'matching-run',
  kind: 'simulation',
  field_id: context.field_id,
  status: 'succeeded',
  attempts: 1,
  result,
  error: null,
  created_at: '2026-08-01T10:00:00Z',
  updated_at: '2026-08-01T10:00:01Z',
}
const forecast: WaterForecast = {
  analysis: 'water_forecast',
  field_id: 'F-03',
  season: 2026,
  crop: 'Maize',
  initialized_at: '2026-08-01T00:00:00Z',
  generated_at: '2026-08-02T10:00:00Z',
  method: 'fao56',
  model_version: null,
  physical_model_version: 'fao56-reference-loam-v1',
  provider: 'ECMWF IFS HRES',
  source_url: 'https://open-meteo.com/en/docs/single-runs-api',
  grid_resolution_km: 9,
  root_depth_m: 1,
  initial_reserve_mm: 110,
  capacity_mm: 150,
  threshold_mm: 67.5,
  field_capacity: 0.3,
  wilting_point: 0.15,
  crop_coefficient: 1.2,
  limitations: [],
  days: [],
}
const forecastJob: Job = { ...job, analysis: 'water_forecast', result: forecast }

test('a successful simulation is available only for its exact field, season, and scenario', () => {
  assert.equal(matchingWaterResult(job, context), result)
  assert.equal(matchingWaterResult(job, { ...context, field_id: 'F-02' }), undefined)
  assert.equal(matchingWaterResult(job, { ...context, season: 2027 }), undefined)
  assert.equal(matchingWaterResult(job, { ...context, scenario: 'less_rain' }), undefined)
})

test('unfinished, failed, and non-simulation jobs cannot explain a water run', () => {
  assert.equal(matchingWaterResult({ ...job, status: 'running' }, context), undefined)
  assert.equal(matchingWaterResult({ ...job, status: 'failed' }, context), undefined)
  assert.equal(matchingWaterResult({ ...job, kind: 'geo_ml' }, context), undefined)
  assert.equal(matchingWaterResult({ ...job, result: null }, context), undefined)
})

test('a run for a previous field boundary remains archived instead of describing the current field', () => {
  const previousBoundary = { ...job, boundary_revision: 1 }
  assert.equal(matchingWaterResult(previousBoundary, context, 2), undefined)
  assert.equal(matchingWaterResult(previousBoundary, context, 1), result)
  assert.equal(matchingWaterResult(job, context, 1), undefined)
})

test('a root-zone forecast remains separate from saved scenario simulations', () => {
  assert.equal(matchingWaterResult(forecastJob, context), undefined)
  assert.equal(selectWaterForecast(context.field_id, job), undefined)
  assert.equal(selectWaterForecast('F-02', forecastJob, forecast), undefined)
  assert.equal(selectWaterForecast(context.field_id, { ...forecastJob, status: 'running' }), undefined)
  assert.equal(selectWaterForecast(context.field_id, forecastJob), forecast)
})

test('the saved forecast is authoritative on ties and a newer issue wins', () => {
  const olderIssue = {
    ...forecast, initialized_at: '2026-07-31T00:00:00Z', generated_at: '2026-08-02T11:00:00Z',
  }
  assert.equal(selectWaterForecast(context.field_id, { ...forecastJob, result: olderIssue }, forecast), forecast)

  const manual = { ...forecast, method: 'ml' as const, model_version: 'retired-candidate' }
  assert.equal(selectWaterForecast(context.field_id, { ...forecastJob, result: manual }, forecast), forecast)

  const newerRun = { ...forecast, generated_at: '2026-08-02T11:00:00Z' }
  assert.equal(selectWaterForecast(context.field_id, { ...forecastJob, result: newerRun }, forecast), newerRun)
})
