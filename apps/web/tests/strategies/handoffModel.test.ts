import assert from 'node:assert/strict'
import test from 'node:test'
import { activeJob, forecastParameters, matchesJob, newCrop, newCycle, newDraft, newSubmission, rankedRecommendations } from '../../src/features/strategies/handoff/model.ts'
import { catalogFixture, filledDraft, jobFixture, resultFixture } from './handoffFixtures.ts'
const today = '2026-11-15'

test('catalog identity and explicit calendars survive without invented field or economic defaults', () => {
  const catalog = catalogFixture(), draft = filledDraft()
  assert.equal(catalog.crops.length, 13)
  assert.equal(catalog.crops.filter(row => row.id.startsWith('wheat_')).length, 3)
  const body = forecastParameters(draft, catalog, today)
  assert.deepEqual(Object.keys(body).sort(), ['farmer', 'live_days', 'plans', 'seasons'])
  const plans = body.plans as Array<{ crops: Array<Record<string, unknown>> }>
  assert.deepEqual(plans[0].crops[0], { crop_id: 'maize_grain', calendar_id: draft.plans[0].crops[0].calendarId })
  assert.equal(JSON.stringify(body).includes('marketable_yield'), false)
  assert.equal(JSON.stringify(body).includes('equipment_ids'), false)
  assert.equal(JSON.stringify(body).includes('initial_moisture'), false)
  assert.deepEqual(body.farmer, { priorities: [], irrigation_available: null })
  assert.equal(newDraft(false).irrigationAccess, 'no')
  assert.equal(newDraft().allowPublicData, true) // explicit Calculate acquisition; transport default stays false
})
test('empty inputs and unsupported calendars block rather than selecting defaults', () => {
  assert.throws(() => forecastParameters(newDraft(), catalogFixture(), today), /planting_date/)
  const draft = filledDraft(); draft.plans[0].crops[0].calendarId = ''
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /calendar/)
  draft.plans[0].crops[0].cropId = 'rapeseed_canola'
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /calendar/)
})
test('cycle overlap, duplicate dates, past dates and three-year finish limit are enforced', () => {
  const catalog = catalogFixture(), draft = filledDraft()
  draft.cycles.push({ ...newCycle(), planting: '2027-05-02' })
  draft.plans.forEach(plan => plan.crops.push({ ...plan.crops[0] }))
  assert.throws(() => forecastParameters(draft, catalog, today), /overlap/)
  draft.cycles[1].planting = draft.cycles[0].planting
  assert.throws(() => forecastParameters(draft, catalog, today), /planting_date/)
  draft.cycles[1].planting = '2028-05-01'
  assert.equal((forecastParameters(draft, catalog, today).seasons as unknown[]).length, 2)
  draft.cycles[0].planting = '2026-11-14'
  assert.throws(() => forecastParameters(draft, catalog, today), /planting_date/)
  const late = filledDraft(); late.cycles[0].planting = '2029-11-15'
  assert.throws(() => forecastParameters(late, catalog, today), /overlap/)
})
test('zero assumptions remain zero, missing values stay absent and financial units require a currency', () => {
  const draft = filledDraft(), crop = draft.plans[0].crops[0]
  Object.assign(crop, { economics: true, yield: '0', price: '', cost: '', source: 'Farmer estimate' })
  const parameters = forecastParameters(draft, catalogFixture(), today)
  assert.deepEqual((parameters.plans as Array<{ crops: Array<{ economics: unknown }> }>)[0].crops[0].economics, { marketable_yield_t_ha: 0, source: 'Farmer estimate' })
  crop.price = '0'
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /economics/)
  crop.currency = 'EUR'; crop.cost = '12'
  assert.doesNotThrow(() => forecastParameters(draft, catalogFixture(), today))
  crop.yield = '-1'; assert.throws(() => forecastParameters(draft, catalogFixture(), today), /economics/)
})
test('opening moisture requires all three layer fractions and a source', () => {
  const draft = filledDraft(), cycle = draft.cycles[0]
  cycle.moisture = true; cycle.theta = ['0', '0.2', '0.3']
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /moisture/)
  cycle.moistureSource = 'Observed three-layer profile'
  assert.deepEqual((forecastParameters(draft, catalogFixture(), today).seasons as Array<{ initial_moisture: unknown }>)[0].initial_moisture, { theta: [0, 0.2, 0.3], source: cycle.moistureSource })
  cycle.theta[2] = ''; assert.throws(() => forecastParameters(draft, catalogFixture(), today), /moisture/)
})
test('irrigation dates and resources are explicit and cannot be inferred to pass checks', () => {
  const draft = filledDraft()
  draft.plans[0].irrigation = [{ date: '2027-05-03', mm: '0' }]
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /irrigation/)
  draft.plans[0].irrigation[0].mm = '20'
  draft.equipment = { row_planter: 'contracted' }; draft.priority = 'water_deficit'
  assert.deepEqual((forecastParameters(draft, catalogFixture(), today).farmer as Record<string, unknown>).equipment_access, { row_planter: 'contracted' })
  draft.plans[0].irrigation.push({ date: '2027-05-03', mm: '2' })
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /irrigation/)
  draft.plans[0].irrigation = [{ date: '2028-01-01', mm: '20' }]
  assert.throws(() => forecastParameters(draft, catalogFixture(), today), /irrigation/)
})
test('recommendations require the envelope gate, actual rank and resource feasibility', () => {
  const result = resultFixture()
  assert.equal(rankedRecommendations(result.payload).length, 0)
  assert.deepEqual(rankedRecommendations(result.payload, true).map(row => row.id), ['plan-a'])
  const decision = result.payload.decision as { decisions: Array<{ feasibility: string; rank: number }>; priorities_used: string[] }
  decision.decisions[0].feasibility = 'needs_confirmation'
  assert.equal(rankedRecommendations(result.payload, true).length, 0)
  decision.decisions[0].feasibility = 'feasible'; decision.priorities_used = []
  assert.equal(rankedRecommendations(result.payload, true).length, 0)
})
test('job identity includes field, boundary and saved job; submission is separate from polling', () => {
  const job = jobFixture()
  assert.equal(matchesJob(job, 'north', 1, 'saved-model-job'), true)
  assert.equal(matchesJob(job, 'south', 1), false)
  assert.equal(matchesJob(job, 'north', 2), false)
  assert.equal(matchesJob(job, 'north', 1, 'other-job'), false)
  assert.equal(activeJob({ ...job, status: 'retrying' }), true)
  assert.equal(activeJob(job), false)
  assert.deepEqual(newSubmission({ seasons: [] }, 4, false, 'retry-1'), { request_id: 'retry-1', geometry_revision: 4, operation: 'forecast', parameters: { seasons: [] }, allow_public_data: false })
  assert.notEqual(newCrop(), newCrop())
})
