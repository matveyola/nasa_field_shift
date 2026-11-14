import assert from 'node:assert/strict'
import test from 'node:test'
import type { WaterDay } from '../src/api/contracts.ts'
import { cumulativeWaterBudget } from '../src/features/water/waterBudget.ts'

function day(date: string, opening: number, rain = 0, irrigation = 0, use = 0, drainage = 0): WaterDay {
  return { date, opening_mm: opening, reserve_mm: opening + rain + irrigation - use - drainage,
    rain_mm: rain, irrigation_mm: irrigation, water_use_mm: use, drainage_mm: drainage }
}

test('cumulative balance conserves water, separates drainage, and does not mutate its input', () => {
  const first = day('2026-11-10', 100, 2, 8, 4, 3)
  const second = day('2026-11-11', 103, 0, 0, 5, 0)
  const input = [second, first]
  const result = cumulativeWaterBudget(input)
  assert.equal(result.kind, 'available')
  assert.equal(result.opening, 100)
  assert.equal(result.closing, 98)
  assert.deepEqual(result.totals, { date: '2026-11-11', rain: 2, irrigation: 8, use: 9, drainage: 3,
    incoming: 10, outgoing: 12, change: -2, reserve: 98 })
  assert.equal(input[0], second)
})

test('a rolling forecast starts from its first remaining day opening, not the original run', () => {
  const days = [day('2026-11-09', 100, 10, 0, 4), day('2026-11-10', 106, 0, 0, 3), day('2026-11-11', 103, 0, 0, 4)]
  const result = cumulativeWaterBudget(days.slice(1))
  assert.equal(result.kind, 'available')
  assert.equal(result.opening, 106)
  assert.equal(result.totals.rain, 0)
  assert.equal(result.totals.change, -7)
})

test('zero precipitation is a value and a one-day balance remains available', () => {
  const result = cumulativeWaterBudget([day('2026-11-10', 0)])
  assert.equal(result.kind, 'available')
  assert.equal(result.totals.incoming, 0)
  assert.equal(result.totals.outgoing, 0)
  assert.equal(result.closing, 0)
})

test('empty, missing, negative and invalid-date data do not become zero totals', () => {
  assert.deepEqual(cumulativeWaterBudget([]), { kind: 'unavailable', reason: 'empty' })
  for (const invalid of [
    { ...day('2026-11-10', 100), rain_mm: Number.NaN },
    { ...day('2026-11-10', 100), drainage_mm: undefined as unknown as number },
    { ...day('2026-11-10', 100), water_use_mm: -1 },
    { ...day('2026-11-10', 100), reserve_mm: Infinity },
    day('2026-11-31', 100),
  ]) assert.deepEqual(cumulativeWaterBudget([invalid]), { kind: 'unavailable', reason: 'invalid' })
})

test('duplicate days and missing days are not silently stitched into a continuous balance', () => {
  for (const secondDate of ['2026-11-10', '2026-11-12']) {
    assert.deepEqual(cumulativeWaterBudget([day('2026-11-10', 100), day(secondDate, 100)]), { kind: 'unavailable', reason: 'gap' })
  }
})

test('unexplained reserve adjustment and mismatched daily fluxes are rejected', () => {
  assert.deepEqual(cumulativeWaterBudget([day('2026-11-10', 100), day('2026-11-11', 102)]), { kind: 'unavailable', reason: 'inconsistent' })
  assert.deepEqual(cumulativeWaterBudget([{ ...day('2026-11-10', 100, 1), reserve_mm: 99 }]), { kind: 'unavailable', reason: 'inconsistent' })
})

test('model serialization rounding is tolerated without concealing a material residual', () => {
  const rounded = { ...day('2026-11-10', 100, .6667, 0, .3333), reserve_mm: 100.3333 }
  assert.equal(cumulativeWaterBudget([rounded]).kind, 'available')
  assert.deepEqual(cumulativeWaterBudget([{ ...rounded, reserve_mm: 100.3 }]), { kind: 'unavailable', reason: 'inconsistent' })
})
