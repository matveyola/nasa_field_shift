import assert from 'node:assert/strict'
import test from 'node:test'
import { fixture, cases, now, expectedStatus } from './fixtures.ts'
import { applyRequest, assignmentDiff, cellEvidence, distinctPlans, metricText, nullableNumber, prepareRequest, runLabel, staleRun, worstScenario } from '../../src/features/planning/model.ts'
import { profileWrite } from '../../src/features/field-profile/model.ts'
import { decode } from '../../src/features/planning/contracts.ts'
import { createPlanningApi, type PlanningTransport } from '../../src/api/planningV1.ts'

test('all frozen geographic specifications have typed synthetic UI responses', () => {
  assert.equal(cases.length, 24)
  for (const spec of cases) {
    const { run, evidence } = fixture(spec.id)
    assert.equal(decode('run', run).request.weather.scenario_type, 'synthetic')
    assert.equal(decode('candidate', evidence.candidates[0]).assessments[0].status, expectedStatus[spec.id])
    decode('evaluation', evidence.evaluations[0])
    assert.ok(run.request.fields[0].missing_data[0].message.includes(spec.title))
    if (expectedStatus[spec.id] !== 'eligible') assert.equal(run.comparison?.alternatives.length, 0)
  }
})
test('missing values remain unknown while real zero and negative margins survive', () => {
  assert.equal(nullableNumber(''), null)
  assert.equal(nullableNumber('0'), 0)
  assert.throws(() => nullableNumber('Infinity'))
  const metric = fixture().run.comparison!.alternatives[0].scenario_results[0].gross_margin
  assert.equal(metricText(metric, 'en', 'UAH'), 'insufficient_data')
  metric.status = 'available'; metric.value = -50
  assert.equal(metricText(metric, 'en', 'UAH'), '-50 UAH')
  metric.value = 0
  assert.equal(metricText(metric, 'en', 'UAH'), '0 UAH')
  assert.equal(metricText(metric, 'en', null), 'Unknown')
})
test('evidence preserves yield bounds and does not invent profit or probabilities', () => {
  const { run, evidence } = fixture('G23')
  const plan = run.comparison!.alternatives[0]
  const details = cellEvidence(run, plan, plan.assignments[0], 'reference', evidence)
  assert.equal(metricText(details.seasons[0].yield_t_ha, 'en'), '3 (2–5) t/ha · Scenario range')
  assert.equal(worstScenario(plan, run.request), null)
  assert.equal(cellEvidence(run, plan, plan.assignments[0], 'not-this-member', evidence).seasons.length, 0)
  evidence.evaluations[0].field_snapshot_id = 'other-field'
  assert.equal(cellEvidence(run, plan, plan.assignments[0], 'reference', evidence).seasons.length, 0)
})
test('nearby or distant cases do not fabricate model values based on coordinates', () => {
  for (const id of ['G12', 'G13', 'G14', 'G07', 'G08']) {
    const { evidence } = fixture(id)
    assert.equal(evidence.evaluations[0].seasons[0].yield_t_ha.value, null)
  }
})
test('duplicate alternatives are removed by assignments, not their labels', () => {
  const plans = fixture().run.comparison!.alternatives
  assert.equal(distinctPlans([...plans, { ...plans[0], alternative_id: 'renamed' }]).length, 2)
})
test('draft submission preserves profiles, zero budgets and committed decisions', () => {
  const { bundle } = fixture()
  const before = JSON.stringify(bundle.request)
  const request = prepareRequest(bundle.request, ['G23'], 'idempotent-request', now())
  assert.equal(request.season_count, 3)
  assert.deepEqual(request.fields, bundle.request.fields)
  assert.equal(JSON.stringify(bundle.request), before)
  bundle.request.locks = [{ ...bundle.candidate.assignments[0], locked: true, commitment_id: 'seed' }]
  assert.throws(() => prepareRequest(bundle.request, [], 'retry', now()))
  bundle.request.resources = [{ constraint_id: 'budget', season: 2027, budget: 0, currency: 'UAH', irrigation_m3: 0 }]
  assert.equal(prepareRequest(bundle.request, ['G23'], 'retry', now()).resources[0].budget, 0)
})
test('stale/future weather or foreign farm scopes cannot be submitted', () => {
  const request = fixture().bundle.request
  assert.throws(() => prepareRequest(request, ['G23'], 'id', new Date('2026-11-16T00:00:00Z')), /Stale/)
  assert.throws(() => prepareRequest(request, ['G23'], 'id', new Date('2026-11-13T00:00:00Z')), /unavailable/)
  request.fields[0].farm_id = 'foreign'
  assert.throws(() => prepareRequest(request, ['G23'], 'id', now()), /scope/)
})
test('a new profile/geometry revision or expired forecast disables old results', () => {
  const { run } = fixture()
  const fields = structuredClone(run.request.fields)
  assert.equal(staleRun(run, fields, run.request.weather, now()), false)
  fields[0].geometry_revision += 1
  assert.equal(staleRun(run, fields, run.request.weather, now()), true)
  assert.equal(staleRun(run, run.request.fields, run.request.weather, new Date('2026-11-15T00:00:00Z')), true)
})
test('run and solver statuses are distinct and infeasible runs cannot be applied', () => {
  const { run } = fixture('G21')
  assert.equal(runLabel(run), 'No feasible plan')
  assert.throws(() => applyRequest(run, 'none', 'request'))
  for (const status of ['queued', 'running', 'failed', 'cancelled'] as const) {
    run.status = status
    assert.notEqual(runLabel(run), 'Compare plans')
  }
})
test('apply carries immutable snapshot checks, and diff includes cultivar/lock changes', () => {
  const { run } = fixture()
  const plan = run.comparison!.alternatives[0]
  const body = applyRequest(run, plan.alternative_id, 'apply-key')
  assert.equal(body.confirmed_by_human, true)
  assert.equal(body.expected_input_sha256, run.input_sha256)
  assert.deepEqual(body.expected_snapshots, run.comparison!.snapshots)
  const changed = structuredClone(plan)
  changed.assignments[0].crop.cultivar_id = 'new-cultivar'
  assert.equal(assignmentDiff(plan, changed).length, 1)
})
test('worst outcome requires every season, currency and trajectory', () => {
  const { run } = fixture()
  const plan = run.comparison!.alternatives[0]
  plan.scenario_results.forEach(row => { row.currency = 'UAH'; row.gross_margin.status = 'available'; row.gross_margin.value = -10 })
  assert.deepEqual(worstScenario(plan, run.request), { value: -30, currency: 'UAH' })
  plan.scenario_results.pop()
  assert.equal(worstScenario(plan, run.request), null)
})
test('profile edit records user provenance and keeps identity/geometry immutable', async () => {
  const original = fixture().bundle.field_profile
  const edited = structuredClone(original)
  edited.soil[0].ph = 6.5
  edited.history[0].crop = { crop_id: 'Peas', cultivar_id: null, profile_version: null }
  edited.history[0].status = 'reported'
  const body = await profileWrite(original, edited, 'edit-1', now())
  assert.equal(body.expected_revision, original.profile_revision)
  assert.equal(body.profile.profile_revision, original.profile_revision + 1)
  assert.equal(body.profile.sources.at(-1)?.kind, 'user_entered')
  assert.match(body.profile.sources.at(-1)!.checksum_sha256, /^[a-f0-9]{64}$/)
  assert.ok(body.profile.soil[0].source_ids.includes('user-edit-1'))
  assert.equal(original.soil[0].ph, null)
  assert.deepEqual(body.profile.geometry, original.geometry)
  edited.geometry_revision++
  await assert.rejects(profileWrite(original, edited, 'edit-2', now()), /identity/)
})
test('profile edits reject overlap, bad water units/ranges and contradictory irrigation', async () => {
  const original = fixture().bundle.field_profile
  const edited = structuredClone(original)
  edited.soil.push(structuredClone(edited.soil[0]))
  await assert.rejects(profileWrite(original, edited, 'id', now()), /overlap/)
  edited.soil.pop(); edited.management.irrigation[0].max_mm = 50
  await assert.rejects(profileWrite(original, edited, 'id', now()), /explicit zero/)
})
test('API uses reserved routes, idempotency and supplied CSRF-aware transport without mock fallback', async () => {
  const { run } = fixture()
  const calls: { path: string; options?: RequestInit }[] = []
  const transport: PlanningTransport = async <T,>(path: string, options?: RequestInit) => {
    calls.push({ path, options }); return run as T
  }
  const api = createPlanningApi(transport)
  await api.submit(run.request)
  assert.equal(calls[0].path, '/planning-v1/farms/farm-demo/runs')
  assert.equal((calls[0].options?.headers as Record<string, string>)['Idempotency-Key'], run.request.request_id)
  await assert.rejects(createPlanningApi(async () => { throw new Error('offline') }).submit(run.request), /offline/)
  await assert.rejects(createPlanningApi(async <T,>() => ({ malformed: true }) as T).run('farm-demo', 'run-1'))
  await assert.rejects(api.run('another-farm', 'run-1'), /scope mismatch/)
})
test('apply responses require matching request identity and genuine acknowledgement', async () => {
  const { run } = fixture()
  const body = applyRequest(run, run.comparison!.alternatives[0].alternative_id, 'key')
  const invalid = { request_id: 'key', status: 'applied', applied_at: null, reasons: [] }
  const api = createPlanningApi(async <T,>() => invalid as T)
  await assert.rejects(api.apply(body), /timestamp/)
  invalid.request_id = 'other'
  await assert.rejects(api.apply(body), /identity/)
})
