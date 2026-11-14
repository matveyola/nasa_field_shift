import assert from 'node:assert/strict'
import test from 'node:test'
import type { Field, WaterForecast } from '../src/api/contracts.ts'
import { fieldWaterPriority, rankFieldWaterPriorities } from '../src/features/farm/fieldPriority.ts'
import { loadFarmForecasts } from '../src/features/farm/loadFarmForecasts.ts'

const today = '2026-11-10'
function forecast(fieldId: string, values = [80, 70, 60]): WaterForecast {
  return { analysis: 'water_forecast', field_id: fieldId, season: 2026, crop: 'Maize', initialized_at: '2026-11-09T00:00:00Z',
    generated_at: '2026-11-09T06:00:00Z', method: 'fao56', model_version: null, physical_model_version: 'test', provider: 'ECMWF',
    source_url: 'https://www.ecmwf.int', grid_resolution_km: 9, root_depth_m: 1, initial_reserve_mm: 90, capacity_mm: 150,
    threshold_mm: 65, field_capacity: .3, wilting_point: .15, crop_coefficient: 1, limitations: [],
    days: values.map((reserve, index) => ({ date: `2026-11-${10 + index}`, reserve_mm: reserve, root_zone_estimate_mm: 0,
      opening_mm: 90, rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0, lead_days: index + 1 })) }
}
function field(id: string): Field {
  return { id, name: id, crop: 'Maize', area_ha: 10, boundary_revision: 0, planting: null,
    geometry: { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] } }
}

test('field signal uses the physical forecast and identifies threshold crossing from today', () => {
  const result = fieldWaterPriority(forecast('F1'), 'F1', today)
  assert.equal(result.kind, 'shortage')
  assert.equal(result.firstStressDate, '2026-11-12')
  assert.equal(result.daysToStress, 2)
  assert.equal(result.minimumMm, 60)
  assert.equal(result.marginMm, -5)
  assert.deepEqual(result.values, [80, 70, 60])
})

test('threshold equality is clear despite an unrelated ML estimate below the threshold', () => {
  const result = fieldWaterPriority(forecast('F1', [80, 70, 65]), 'F1', today)
  assert.equal(result.kind, 'clear')
  assert.equal(result.firstStressDate, null)
  assert.equal(result.daysToStress, null)
})

test('missing, stale, expired, future, other-field and demo forecasts cannot declare a field clear', () => {
  const cases: [WaterForecast | null, string][] = [
    [null, 'missing'], [forecast('F2'), 'invalid'],
    [{ ...forecast('F1'), initialized_at: '2026-11-05T00:00:00Z' }, 'stale'],
    [{ ...forecast('F1'), initialized_at: '2026-11-11T00:00:00Z' }, 'invalid'],
    [{ ...forecast('F1'), days: forecast('F1').days.map((day) => ({ ...day, date: '2026-11-09' })) }, 'expired'],
    [{ ...forecast('F1'), days: forecast('F1').days.slice(1) }, 'incomplete'],
    [{ ...forecast('F1'), demo: true } as WaterForecast, 'invalid'],
  ]
  for (const [input, reason] of cases) assert.deepEqual(fieldWaterPriority(input, 'F1', today), { kind: 'unavailable', fieldId: 'F1', reason })
})

test('priority ranks date, relative deficit, unknown data and safe margin deterministically without mutation', () => {
  const entries = [
    { priority: fieldWaterPriority(forecast('clear', [100, 100, 100]), 'clear', today) },
    { priority: fieldWaterPriority(null, 'unknown', today) },
    { priority: fieldWaterPriority(forecast('late', [90, 80, 30]), 'late', today) },
    { priority: fieldWaterPriority(forecast('early-severe', [30, 20, 15]), 'early-severe', today) },
    { priority: fieldWaterPriority(forecast('early', [60, 55, 50]), 'early', today) },
  ]
  assert.deepEqual(rankFieldWaterPriorities(entries).map(({ priority }) => priority.fieldId), ['early-severe', 'early', 'late', 'unknown', 'clear'])
  assert.equal(entries[0].priority.fieldId, 'clear')
})

test('loading a farm limits concurrency, keeps field identity and isolates request failures', async () => {
  let active = 0, maximum = 0
  const fields = Array.from({ length: 8 }, (_, index) => field('F' + index))
  const results = await loadFarmForecasts(fields, today, new AbortController().signal, async (id) => {
    active++
    maximum = Math.max(maximum, active)
    await new Promise((resolve) => setTimeout(resolve, 2))
    active--
    if (id === 'F2') throw new Error('Network error')
    return forecast(id)
  })
  assert.equal(maximum, 3)
  assert.equal(results.length, 8)
  assert.deepEqual(results.map(({ field }) => field.id), fields.map(({ id }) => id))
  assert.deepEqual(results[2].priority, { kind: 'unavailable', fieldId: 'F2', reason: 'request_failed' })
  assert.equal(results[7].priority.kind, 'shortage')
})

test('an aborted farm request stops dequeuing and does not return a partially successful ranking', async () => {
  const controller = new AbortController()
  let started = 0
  await assert.rejects(loadFarmForecasts(Array.from({ length: 10 }, (_, index) => field('F' + index)), today, controller.signal, async (id) => {
    started++
    controller.abort()
    return forecast(id)
  }), { name: 'AbortError' })
  assert.equal(started, 1)
})
