import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Field, WaterForecast } from '../../src/api/contracts'
import { currentWater } from '../../src/features/analytics/currentWater.ts'

const field = { id: 'field-a', crop: 'Maize' } as Field
const today = '2026-11-15'
const forecast = { analysis: 'water_forecast', field_id: field.id, crop: 'Maize', season: 2026,
  initialized_at: '2026-11-15T00:00:00Z', threshold_mm: 50,
  days: [50, 0].map((reserve_mm, i) => ({date: `2026-11-${15+i}`,reserve_mm,opening_mm:50,rain_mm:0,irrigation_mm:0,water_use_mm:50-reserve_mm,drainage_mm:0,root_zone_estimate_mm:999,lead_days:i})),
} as WaterForecast

test('summary and evidence retain physical values, valid zero and strict threshold', () => {
  const actual = currentWater(field, forecast, today)
  assert.equal(actual.current, forecast)
  assert.equal(actual.first.reserve_mm, 50)
  assert.equal(actual.metrics?.firstBelow, '2026-11-16')
  assert.equal(actual.metrics?.minimum?.reserve_mm, 0)
  assert.equal(actual.metrics?.stressDays, 1)
  assert.equal(actual.outlook.kind, 'shortage')
  assert.deepEqual(forecast.days.map(day => day.reserve_mm), [50, 0])
})
test('unknown crops, mismatched saved crop or field, stale and missing estimates stay unavailable', () => {
  const cases: [Field, WaterForecast | null][] = [
    [{...field,crop:''},forecast], [{...field,crop:'Unknown'},forecast], [{...field,crop:'Rice'},forecast],
    [{...field,crop:'Soy'},forecast], [{...field,id:'field-b'},forecast], [field,null],
    [field,{...forecast,initialized_at:'2026-11-10T00:00:00Z'}],
    [field,{...forecast,days:forecast.days.slice(1)}],
  ]
  for (const [selected, saved] of cases) {
    const actual = currentWater(selected,saved,today)
    assert.equal(actual.current,undefined)
    assert.equal(actual.first,undefined)
    assert.equal(actual.metrics,null)
    assert.equal(actual.outlook.kind,'unavailable')
  }
})
test('crop normalization preserves existing strict aliases rather than swapping saved crop assumptions', () => {
  assert.equal(currentWater({...field,crop:' maize '},forecast,today).current,forecast)
  assert.equal(currentWater({...field,crop:'Corn'},forecast,today).current,undefined)
  assert.equal(currentWater({...field,crop:'Corn'},{...forecast,crop:'corn'},today).first.reserve_mm,50)
})
