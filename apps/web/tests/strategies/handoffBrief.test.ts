import assert from 'node:assert/strict'
import test from 'node:test'
import { canUseRanking, decisionBrief, weatherBasis } from '../../src/features/strategies/handoff/brief.ts'
import { resultFixture } from './handoffFixtures.ts'

function cycles(result: ReturnType<typeof resultFixture>) {
  return (result.payload.plans as any[]).flatMap(plan => plan.members.flatMap((member: any) => member.seasons))
}

test('actual-result-shaped analogue-only job is not a short forecast or recommendation', () => {
  const result = resultFixture(false)
  result.suitability_status = 'not_assessed'; result.diagnostics = []
  result.payload.decision = null
  result.payload.weather = { live_source_url: null, member_count: 5 }
  assert.equal(weatherBasis(result), 'historical')
  assert.deepEqual(decisionBrief(result).checks, ['land', 'resources'])
  assert.deepEqual(decisionBrief(result).ranked, [])
})

test('actual complete live segments and mixed segments determine weather basis', () => {
  const result = resultFixture()
  for (const cycle of cycles(result)) cycle.weather_segments = [{ source: 'live_forecast', dates: { start: cycle.planting_date, end: cycle.through_date } }]
  assert.equal(weatherBasis(result), 'live')
  for (const cycle of cycles(result)) cycle.weather_segments = [
    { source: 'live_forecast', dates: { start: '2027-05-01', end: '2027-05-07' } },
    { source: 'historical_analog', dates: { start: '2027-05-08', end: '2027-08-20' }, historical_dates: { start: '2020-05-08', end: '2020-08-20' } },
  ]
  assert.equal(weatherBasis(result), 'mixed')
})

for (const defect of ['missing', 'gap', 'overlap', 'invalid', 'unknown', 'short_history', 'empty_member', 'empty_plans']) test(`incomplete weather evidence stays unspecified: ${defect}`, () => {
  const result = resultFixture(), cycle = cycles(result)[0]
  if (defect === 'missing') delete cycle.weather_segments
  if (defect === 'gap') cycle.weather_segments[0].dates.start = '2027-05-02'
  if (defect === 'overlap') cycle.weather_segments.push(cycle.weather_segments[0])
  if (defect === 'invalid') cycle.weather_segments[0].dates.start = '2027-02-30'
  if (defect === 'unknown') cycle.weather_segments[0].source = 'guessed'
  if (defect === 'short_history') cycle.weather_segments[0].historical_dates.start = '2020-05-02'
  if (defect === 'empty_member') (result.payload.plans as any[])[0].members = []
  if (defect === 'empty_plans') result.payload.plans = []
  assert.equal(weatherBasis(result), 'unknown')
})

test('only actual usable feasible ranks and ties are retained without sorting', () => {
  const result = resultFixture(), rows = (result.payload.decision as any).decisions
  rows[1].rank = 1
  assert.deepEqual(decisionBrief(result).ranked, ['plan-a', 'plan-b'])
  rows[0].feasibility = 'infeasible'
  assert.deepEqual(decisionBrief(result).ranked, ['plan-b'])
  assert.ok(decisionBrief(result).checks.includes('resources'))
  result.status = 'unavailable'
  assert.deepEqual(decisionBrief(result).ranked, [])
  assert.equal(decisionBrief(result).canCompare, false)
})

for (const status of ['not_assessed', 'review_required', 'no_thermal_growth'] as const) test(`raw rank cannot override ${status}`, () => {
  const result = resultFixture(); result.suitability_status = status
  assert.equal(canUseRanking(result), false)
  assert.deepEqual(decisionBrief(result).ranked, [])
})

test('blocking diagnostic overrides contradictory true ranking flag', () => {
  const result = resultFixture(); result.diagnostics = [{ code: 'review', severity: 'blocking', message: 'test', crop_id: null, start_date: null, end_date: null, details: {} }]
  assert.equal(canUseRanking(result), false)
  assert.ok(decisionBrief(result).checks.includes('review'))
})

test('null margin is not zero; currency mismatch is surfaced without conversion or profit rank', () => {
  const result = resultFixture(), plans = result.payload.plans as any[]
  plans.forEach(plan => { plan.economics.gross_revenue_per_ha = 0; plan.economics.gross_revenue_currency = 'USD'; plan.economics.gross_margin_per_ha = 0 })
  assert.ok(!decisionBrief(result).checks.includes('costs'))
  plans[0].economics.gross_margin_per_ha = null
  assert.ok(decisionBrief(result).checks.includes('costs'))
  plans[1].economics.gross_revenue_currency = 'EUR'
  assert.equal(decisionBrief(result).checks[0], 'currencies')
  ;(result.payload.decision as any).priorities_used = []
  assert.deepEqual(decisionBrief(result).ranked, [])
})

test('different periods and zero/null irrigation do not create advantage or new rank', () => {
  const result = resultFixture(); result.payload.decision = null
  const plans = result.payload.plans as any[]
  plans[0].scheduled_net_irrigation_mm = 0; plans[1].scheduled_net_irrigation_mm = null
  const cycle = cycles(result)[1]
  cycle.through_date = '2027-09-01'; cycle.weather_segments[0].dates.end = '2027-09-01'; cycle.weather_segments[0].historical_dates.end = '2020-09-01'
  assert.equal(weatherBasis(result), 'historical')
  const before = JSON.stringify(result)
  assert.deepEqual(decisionBrief(result).ranked, [])
  assert.equal(JSON.stringify(result), before)
})
