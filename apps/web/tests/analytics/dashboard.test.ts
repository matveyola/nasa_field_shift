import assert from 'node:assert/strict'
import test from 'node:test'
import type { Field, WaterForecast } from '../../src/api/contracts.ts'
import { droughtWindow, shiftDay, weatherTotals } from '../../src/features/analytics/dashboardModel.ts'

const today = '2026-11-15'
const field: Field = {
  id: 'one', name: 'One', crop: 'Maize', area_ha: 10, boundary_revision: 3, planting: null,
  geometry: { type: 'Polygon', coordinates: [[[30, 50], [31, 50], [31, 51], [30, 50]]] },
}
const period = { start: today, end: shiftDay(today, 29) }

function forecast(count: number, risky: number[] = []): WaterForecast {
  const days = Array.from({ length: count }, (_, i) => ({
    date: shiftDay(today, i), reserve_mm: risky.includes(i) ? 25 : 80, opening_mm: 80,
    rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0, lead_days: i + 1, root_zone_estimate_mm: 120,
  }))
  return {
    field_id: field.id, season: 2026, crop: 'Maize', analysis: 'water_forecast',
    initialized_at: '2026-11-14T00:00:00Z', generated_at: today,
    method: 'fao56', model_version: null, physical_model_version: 'fixture', provider: 'Fixture',
    source_url: 'https://example.test', grid_resolution_km: 9, root_depth_m: 1,
    initial_reserve_mm: 80, capacity_mm: 120, threshold_mm: 40, field_capacity: .3,
    wilting_point: .15, crop_coefficient: 1.1, days, limitations: [],
    applicability: { status: 'not_assessed', source_kind: 'forecast', source_variable: 'soil_temperature_0_to_7cm_mean',
      depth_cm: [0, 7], unit: '°C', aggregation: 'daily_mean', initialized_at: today,
      soil_temperature: days.map(({ date }) => ({ date, mean_c: 15 })) },
  }
}

test('only a complete, applicable operational window can clear a 30-day drought message', () => {
  const complete = droughtWindow(field, forecast(30), today, period)
  assert.equal(complete.covered.length, 30)
  assert.equal(complete.complete, true)
  assert.equal(complete.applicable, true)
  assert.deepEqual(complete.risks, [])
  const partial = droughtWindow(field, forecast(5), today, period)
  assert.equal(partial.complete, false)
  assert.equal(partial.covered.length, 5)
  assert.equal(droughtWindow(field, forecast(31, [30]), today, period).risks.length, 0)
  const legacy = forecast(30)
  legacy.applicability = null
  assert.equal(droughtWindow(field, legacy, today, period).applicable, false)
})

test('uses physical reserves and reports distinct warning intervals within the selected dates', () => {
  const model = forecast(12, [0, 1, 3, 4, 5, 7])
  const selected = { start: shiftDay(today, 1), end: shiftDay(today, 6) }
  const result = droughtWindow(field, model, today, selected)
  assert.equal(result.live, false)
  assert.equal(result.complete, true)
  assert.deepEqual(result.period, selected)
  assert.deepEqual(result.risks, [
    { start: shiftDay(today, 1), end: shiftDay(today, 1) },
    { start: shiftDay(today, 3), end: shiftDay(today, 5) },
  ])
  model.applicability!.soil_temperature[1].mean_c = 0
  assert.equal(droughtWindow(field, model, today, selected).applicable, false)
})

test('missing, stale, historical, foreign and crop-mismatched signals provide no coverage', () => {
  const model = forecast(30)
  assert.equal(droughtWindow(field, undefined, today, period).reason, 'missing')
  assert.equal(droughtWindow(field, { ...model, initialized_at: '2026-11-11' }, today, period).reason, 'stale')
  assert.equal(droughtWindow(field, { ...model, field_id: 'two' }, today, period).reason, 'invalid')
  assert.equal(droughtWindow({ ...field, crop: 'Barley' }, model, today, period).covered.length, 0)
  assert.equal(droughtWindow(field, model, today, { start: '2025-10-15', end: '2025-10-17' }).reason, 'historical')
})

test('period rain sums keep source boundaries and preserve zero observations', () => {
  assert.deepEqual(weatherTotals([
    { start: today, end: today, source: 'forecast', reason: null, points: [{ start: today, end: today, value: 0 }] },
    { start: '2026-11-16', end: '2026-11-18', source: 'climate', reason: null,
      points: [{ start: '2026-11-16', end: '2026-11-18', value: 1.5 }] },
  ]).map(({ source, total }) => [source, total]), [['forecast', 0], ['climate', 4.5]])
})
