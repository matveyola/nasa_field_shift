import assert from 'node:assert/strict'
import test from 'node:test'
import { seasonalWeatherMetrics } from '../src/features/fields/seasonalWeatherMetrics.ts'

test('seasonal totals preserve missing days and distinguish a valid zero', () => {
  const days = [
    { date: '2026-01-01', temperature_min_c: -5, temperature_max_c: 1, precipitation_mm: 0 },
    { date: '2026-01-02', temperature_min_c: null, temperature_max_c: 18, precipitation_mm: null },
    { date: '2026-01-03', temperature_min_c: 12, temperature_max_c: 20, precipitation_mm: 3 },
  ]
  const result = seasonalWeatherMetrics(days, 10)
  assert.deepEqual(result.points.map((point) => point.cumulativePrecipitation), [0, null, 3])
  assert.deepEqual(result.points.map((point) => point.cumulativeDegreeDays), [0, null, 6])
  assert.deepEqual(result.points.map((point) => point.dailyDegreeDays), [0, null, 6])
  assert.equal(result.precipitation, 3)
  assert.equal(result.degreeDays, 6)
  assert.equal(result.precipitationDays, 2)
  assert.equal(result.temperatureDays, 2)
  assert.equal(seasonalWeatherMetrics(days, 5).degreeDays, 11)
})

test('a wholly missing weather series has unavailable totals', () => {
  const result = seasonalWeatherMetrics([
    { date: '2026-01-01', temperature_min_c: null, temperature_max_c: null, precipitation_mm: null },
  ], 10)
  assert.equal(result.precipitation, null)
  assert.equal(result.degreeDays, null)
  assert.equal(result.precipitationDays, 0)
  assert.equal(result.temperatureDays, 0)
})
