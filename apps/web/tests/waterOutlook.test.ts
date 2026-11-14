import assert from 'node:assert/strict'
import test from 'node:test'
import type { WaterForecast } from '../src/api/contracts.ts'
import { getCurrentWaterOutlook, getWaterOutlook } from '../src/features/water/waterOutlook.ts'

const fieldId = 'F-03'
const today = '2026-11-10'

function day(date: string, reserve: number, estimate: number | null = null): WaterForecast['days'][number] {
  return {
    date, reserve_mm: reserve, root_zone_estimate_mm: estimate, lead_days: 1,
    opening_mm: 90, rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0,
  }
}

function forecast(...days: WaterForecast['days']) {
  return { field_id: fieldId, initialized_at: '2026-11-09T00:00:00Z', threshold_mm: 65, days }
}

test('shortage identifies the first upcoming day below the physical threshold', () => {
  const input = forecast(day('2026-11-12', 35), day('2026-11-09', 20),
    day(today, 65, 44), day('2026-11-11', 54, 75))
  const outlook = getWaterOutlook(input, fieldId, today)

  assert.equal(outlook.kind, 'shortage')
  assert.equal(outlook.firstShortage.date, '2026-11-11')
  assert.equal(outlook.minimum.date, '2026-11-12')
  assert.equal(outlook.firstDay.date, today)
  assert.equal(outlook.lastDay.date, '2026-11-12')
  assert.equal(input.days[0].date, '2026-11-12')
})

test('a reserve equal to the threshold is clear even if an ML estimate is lower', () => {
  const outlook = getWaterOutlook(forecast(day('2026-11-11', 65, 12), day('2026-11-12', 70)), fieldId, today)

  assert.equal(outlook.kind, 'clear')
  assert.equal(outlook.firstDay.date, '2026-11-11')
  assert.equal(outlook.minimum.reserve_mm, 65)
})

test('missing, expired, and other-field data cannot produce a shortage or a clear status', () => {
  assert.deepEqual(getWaterOutlook(null, fieldId, today), { kind: 'unavailable', reason: 'missing' })
  assert.deepEqual(getWaterOutlook(forecast(), fieldId, today), { kind: 'unavailable', reason: 'missing' })
  assert.deepEqual(getWaterOutlook(forecast(day('2026-11-09', 5)), fieldId, today),
    { kind: 'unavailable', reason: 'expired' })
  assert.deepEqual(getWaterOutlook(forecast(day(today, 5)), 'F-02', today),
    { kind: 'unavailable', reason: 'invalid' })
})

test('incomplete or malformed data cannot be presented as a reliable outlook', () => {
  const invalid = [
    forecast(day(today, Number.NaN)),
    forecast(day('2026-11-31', 100)),
    forecast(day(today, 100), day(today, 105)),
    forecast(day(today, 100), day('2026-11-12', 105)),
    { ...forecast(day(today, 100)), threshold_mm: Number.POSITIVE_INFINITY },
    { ...forecast(day(today, 100)), initialized_at: 'unknown' },
  ]
  for (const input of invalid) {
    assert.deepEqual(getWaterOutlook(input, fieldId, today), { kind: 'unavailable', reason: 'invalid' })
  }
})

test('live assistant signals require fresh initialization, today coverage and the selected season', () => {
  const current = { ...forecast(day(today, 70), day('2026-11-11', 60)), analysis: 'water_forecast', season: 2026 } as WaterForecast
  assert.equal(getCurrentWaterOutlook(current, fieldId, today, 2026).kind, 'shortage')
  assert.deepEqual(getCurrentWaterOutlook(current, fieldId, today, 2025), { kind: 'unavailable', reason: 'invalid' })
  assert.deepEqual(getCurrentWaterOutlook({ ...current, initialized_at: '2026-11-06T00:00:00Z' }, fieldId, today), { kind: 'unavailable', reason: 'stale' })
  assert.deepEqual(getCurrentWaterOutlook({ ...current, initialized_at: '2026-11-11T00:00:00Z' }, fieldId, today), { kind: 'unavailable', reason: 'invalid' })
  assert.deepEqual(getCurrentWaterOutlook({ ...current, days: [day('2026-11-11', 70)] }, fieldId, today), { kind: 'unavailable', reason: 'incomplete' })
})
