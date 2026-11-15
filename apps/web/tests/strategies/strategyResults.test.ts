import test from 'node:test'
import assert from 'node:assert/strict'
import type { HandoffResult, StrategyContext } from '../../src/api/rotationHandoff.ts'
import { afterCrop, initialPlantingDate, metric, metricDifference, strategyResult, validPlantingDate } from '../../src/features/strategies/strategyResults.ts'

const a = { crop_id: 'maize_grain', rank: null, status: 'ok' as const, economics: { gross_revenue_per_ha: 12, revenue_after_seed_per_ha: 0, currency: 'USD' } }
const b = { ...a, crop_id: 'barley', economics: { ...a.economics, revenue_after_seed_per_ha: 5 } }

test('priority metrics keep genuine zero and require compatible currency for a delta', () => {
  assert.deepEqual(metric(a, 'revenue_after_seed'), { amount: 0, unit: 'USD/ha' })
  assert.equal(metric(a, 'water_stress'), null)
  assert.deepEqual(metricDifference(a, b, 'revenue_after_seed'), { amount: 5, unit: 'USD/ha' })
  assert.equal(metricDifference(a, { ...b, economics: { ...b.economics, currency: 'EUR' } }, 'revenue_after_seed'), null)
})

test('eligible choices can remain selectable without assigning a rank', () => {
  const c = { ...a, crop_id: 'unsupported-crop', status: 'unsupported' }
  const hybrid = { ...a, choice_id: 'maize-p0987amx' }
  const result = { ranking_usable: true, payload: { candidates: [a, b, c, hybrid], top_candidates: [],
    ranking: { status: 'priority_unavailable', selection_ids: [b.crop_id, a.crop_id, c.crop_id, b.crop_id, hybrid.choice_id] } } } as unknown as HandoffResult
  assert.deepEqual(strategyResult(result), { candidates: [a, b, c, hybrid], picks: [b, a, hybrid], ranked: false })
})

const context: StrategyContext = { field_id: 'f', geometry_revision: 1, area_ha: 10, history: [], min_year: 2022, max_year: 2049, min_month: 3, max_month: 7, max_steps: 6, max_calendar_years: 10 }

test('date validation accepts same-year sowing after harvest and respects the model horizon', () => {
  assert.equal(initialPlantingDate(context, '2026-11-15'), '2027-05-01')
  assert.equal(initialPlantingDate(context, '2026-06-15'), '2026-06-15')
  assert.equal(validPlantingDate('2049-04-01', context), true)
  assert.equal(validPlantingDate('2048-08-01', context), false)
  assert.equal(validPlantingDate('2048-04-01', context, '2048-03-31'), true)
  assert.equal(validPlantingDate('2048-04-01', context, '2048-04-01'), false)
  assert.equal(validPlantingDate('2040-04-01', context, '2031-03-01', '2030-05-01'), false)
})

test('next sowing starts after the latest recovery and honors the sowing window', () => {
  const first = '2040-03-01'
  const scenario = (recovery: string | null) => ({
    end_date: '2040-06-20', post_crop_recharge: {
      status: recovery ? 'reached' : 'not_reached_in_window', target_reached_on: recovery,
      searched_through: '2040-12-31',
    },
  })
  const after = (other: string | null) => afterCrop(context,
    { ...a, scenarios: { first: scenario('2040-06-21'), second: scenario(other) } }, first, first)
  assert.equal(after('2040-06-27').suggested, '2040-06-28')
  assert.equal(after('2040-07-31').suggested, '2041-03-01')
  assert.equal(after(null).suggested, undefined)
  assert.equal(after(null).searchedThrough, '2040-12-31')
})
