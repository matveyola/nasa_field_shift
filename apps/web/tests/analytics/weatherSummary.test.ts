import assert from 'node:assert/strict'
import { test } from 'node:test'
import { temperatureRange } from '../../src/features/analytics/weatherSummary.ts'

test('available temperature range retains zero, negatives and partial readings without filling gaps', () => {
  assert.deepEqual(temperatureRange([{ temperature_c: -3 }, { temperature_c: null }, { temperature_c: 0 }]), { min: -3, max: 0 })
  assert.deepEqual(temperatureRange([{ temperature_c: 7 }]), { min: 7, max: 7 })
})
test('absent or nonfinite temperatures do not manufacture a range', () => {
  assert.equal(temperatureRange([]), null)
  assert.equal(temperatureRange([{ temperature_c: null }, { temperature_c: NaN }, { temperature_c: Infinity }]), null)
})
