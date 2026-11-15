import assert from 'node:assert/strict'
import test from 'node:test'
import { allocationSeasonSupported, initialStrategyView, nextSeasonDecisions } from '../../src/features/strategies/model.ts'
import { objectiveSummary, excludedAssessments, resizePlanningPeriod, prepareRequest, usesUserYield } from '../../src/features/planning/model.ts'
import type { PlanningDraft } from '../../src/api/rotationPlanning'
import { fixture, now } from '../planning/fixtures.ts'

test('allocation starts at the selected season and refuses unsupported years', () => {
  assert.equal(allocationSeasonSupported(2027), true)
  for (const value of [2000, 2101, 2027.5, NaN]) assert.equal(allocationSeasonSupported(value), false)
  assert.equal(initialStrategyView(), 'opportunities')
  assert.equal(initialStrategyView('profiles'), 'scenarios')
  assert.equal(initialStrategyView('economics'), 'scenarios')
})
test('next-season decisions use saved field names and reasons, not later crops or fabricated alternatives', () => {
  const draft = { start_season: 2027, fields: [{ id: 'a', name: 'North' }, { id: 'b', name: 'South' }], result: { assignments: [
    { field_id: 'a', season: 2027, crop: 'Soy', reasons: ['Crop preference 3'], locked: true },
    { field_id: 'b', season: 2028, crop: 'Wheat', reasons: [] },
  ] } } as PlanningDraft
  const original = structuredClone(draft)
  const rows = nextSeasonDecisions(draft, 'b')
  assert.equal(rows[0].field.name, 'South')
  assert.equal(rows[0].assignment, undefined)
  assert.equal(rows[1].assignment?.crop, 'Soy')
  assert.deepEqual(rows[1].assignment?.reasons, ['Crop preference 3'])
  assert.equal(rows[1].assignment?.locked, true)
  assert.deepEqual(draft, original)
  draft.result.assignments = []
  assert.ok(nextSeasonDecisions(draft).every(row => !row.assignment))
})
test('farm outcomes retain actual units, zero, losses and missing currency', () => {
  const { run } = fixture()
  const plan = run.comparison!.alternatives[0]
  run.request.goal = 'reference_margin'; plan.objective_unit = 'currency'; plan.objective_value = -50
  assert.equal(objectiveSummary(plan, run.request).value, null)
  plan.scenario_results.forEach(row => { row.currency = 'EUR' })
  assert.deepEqual(objectiveSummary(plan, run.request), { label: 'Reference scenario margin', value: -50, unit: 'EUR' })
  plan.scenario_results[0].currency = 'USD'
  assert.equal(objectiveSummary(plan, run.request).value, null)
  run.request.goal = 'water_demand'; plan.objective_unit = 'mm_ha'; plan.objective_value = 0
  assert.deepEqual(objectiveSummary(plan, run.request), { label: 'Water demand', value: 0, unit: 'mm·ha' })
  plan.objective_value = null
  assert.equal(objectiveSummary(plan, run.request).value, null)
})
test('why-not explanations only use the inspected field snapshot and season, deduplicated', () => {
  const { run, evidence } = fixture('G09')
  const context = { fieldId: 'G09', season: 2027, trajectoryId: 'reference' }
  evidence.candidates.push(structuredClone(evidence.candidates[0]))
  assert.equal(excludedAssessments(run, evidence, context).length, 1)
  assert.equal(excludedAssessments(run, evidence, context)[0].status, 'unsupported')
  assert.equal(excludedAssessments(run, evidence, { ...context, fieldId: 'unrelated' }).length, 0)
  assert.equal(excludedAssessments(run, evidence, { ...context, season: 2030 }).length, 0)
  evidence.candidates.forEach(candidate => { candidate.field_snapshot_id = 'stale' })
  assert.equal(excludedAssessments(run, evidence, context).length, 0)
})

test('single-season forecasts keep the chosen horizon and exclude later editable assumptions', () => {
  const { run } = fixture()
  const draft = run.request
  draft.resources = [2027, 2028].map(season => ({ constraint_id: `b-${season}`, season, budget: 0, currency: 'EUR', irrigation_m3: 0 }))
  const original = structuredClone(draft)
  const resized = resizePlanningPeriod(draft, 1)
  assert.equal(resized.season_count, 1)
  assert.deepEqual(resized.resources.map(row => row.season), [2027])
  const prepared = prepareRequest(resized, ['G23'], 'one-season-request', now())
  assert.equal(prepared.season_count, 1)
  assert.deepEqual(draft, original)
  draft.locks = [{ ...run.comparison!.alternatives[0].assignments[1], locked: true, commitment_id: 'confirmed-later-season' }]
  assert.throws(() => resizePlanningPeriod(draft, 1), /commitment conflicts/)
  assert.equal(draft.locks[0].commitment_id, 'confirmed-later-season')
})

test('two-season modeled requests are supported without stretching native forecast limits', () => {
  const { run } = fixture()
  const request = prepareRequest(resizePlanningPeriod(run.request, 2), ['G23'], 'two-seasons', now())
  assert.equal(request.season_count, 2)
})

test('financial assumptions are labeled only when linked to the displayed saved alternative', () => {
  const { run } = fixture()
  const plan = run.comparison!.alternatives[0]
  const assumption = { assumption_id: 'assumed-yield', yield_origin: 'user_assumption' } as typeof run.request.economics[number]
  run.request.economics = [assumption]
  assert.equal(usesUserYield(plan, run.request), false)
  plan.scenario_results[0].assumption_ids = ['assumed-yield']
  assert.equal(usesUserYield(plan, run.request), true)
  assumption.yield_origin = 'model'
  assert.equal(usesUserYield(plan, run.request), false)
})
