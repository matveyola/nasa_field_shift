import assert from 'node:assert/strict'
import test from 'node:test'
import type { Job, WaterResult } from '../src/api/contracts.ts'
import {
  assistantPath, conversationKey, currentAssistantRun, explicitHistoricalRun, historicalPeriod, type AssistantRun,
} from '../src/features/assistant/assistantRun.ts'

const RUN = '0b6f4a39-58c4-4d39-9f4d-3c8c25c2f6a1'
const selected: AssistantRun = { id: RUN, fieldId: 'F-03', season: 2026, scenario: 'less_rain' }
const water = { fieldId: 'F-03', season: 2026, scenario: 'less_rain', page: 'water', jobId: RUN }

test('a selected run is explained only while the app still shows its field, season, scenario and run', () => {
  assert.equal(currentAssistantRun(selected, water), RUN)
  // Opened from Water, the conversation may follow the farmer to another page of the same field context.
  assert.equal(currentAssistantRun(selected, { ...water, page: 'fields', jobId: undefined }), RUN)
  for (const changed of [
    { fieldId: 'F-01' }, { fieldId: undefined }, { season: 2025 }, { season: undefined },
    { scenario: 'baseline' }, { scenario: 'dry_year' },
    { jobId: 'another-run' }, { jobId: undefined }, // Water shows another run, or its latest/forecast default
  ]) assert.equal(currentAssistantRun(selected, { ...water, ...changed }), undefined, JSON.stringify(changed))
  assert.equal(currentAssistantRun(undefined, water), undefined) // a run in the address alone is not a selection
})

test('the run is a path segment of the widget base URL, never a query string', () => {
  assert.equal(assistantPath('F-03', 2026, 'less_rain'), '/agent/assistant/F-03/2026/less_rain')
  const path = assistantPath('F-03', 2026, 'less_rain', RUN)
  assert.equal(path, `/agent/assistant/F-03/2026/less_rain/runs/${RUN}`)
  // The widget appends its endpoints to the base: they stay under the run.
  assert.equal(new URL(path + '/v1/agent/stream', 'https://x').pathname, `${path}/v1/agent/stream`)
  const hostile = assistantPath('F 3', 2026, 'less_rain', '../x?y#z')
  assert.equal(hostile, '/agent/assistant/F%203/2026/less_rain/runs/..%2Fx%3Fy%23z')
  assert.equal(new URL(hostile + '/health/ready', 'https://x').search, '')
})

test('another field, boundary revision, season, scenario or run each start a new conversation', () => {
  const base = { fieldId: 'F-03', boundaryRevision: 0, season: 2026, scenario: 'less_rain', runId: RUN }
  const key = conversationKey(base)
  assert.equal(conversationKey({ ...base }), key) // the same field context keeps its conversation
  const changes = {
    fieldId: [{ fieldId: 'F-01' }, { fieldId: undefined }],
    boundaryRevision: [{ boundaryRevision: 1 }, { boundaryRevision: undefined }],
    season: [{ season: 2025 }],
    scenario: [{ scenario: 'baseline' }, { scenario: 'dry_year' }],
    runId: [{ runId: 'another-run' }, { runId: undefined }], // another run, or back to current data
  }
  for (const [name, variants] of Object.entries(changes)) {
    for (const change of variants) {
      assert.notEqual(conversationKey({ ...base, ...change }), key, `${name}: ${JSON.stringify(change)}`)
    }
  }
  // Values cannot run into each other: a separator inside a field id is not another context.
  assert.notEqual(
    conversationKey({ ...base, fieldId: 'F:1', boundaryRevision: 2 }),
    conversationKey({ ...base, fieldId: 'F', boundaryRevision: 12 }),
  )
  assert.notEqual(conversationKey({ ...base, fieldId: 'F-03:0' }), conversationKey({ ...base, fieldId: 'F-03' }))
})

const result = { field_id: 'F-03', season: 2026, scenario: 'less_rain', demo: false, source: 'historical' } as WaterResult
const job = (update: Partial<Job> = {}) => ({ id: RUN, kind: 'simulation', status: 'succeeded', result, ...update }) as Job

test('«Ask agent» hands over the selected finished historical run, not the latest one shown by default', () => {
  assert.equal(explicitHistoricalRun(RUN, job(), result), RUN)
  assert.equal(explicitHistoricalRun(undefined, job(), result), undefined) // the latest run shown without a selection
  assert.equal(explicitHistoricalRun(RUN, job({ id: 'previous' }), result), undefined) // the previous run while the new one loads
  assert.equal(explicitHistoricalRun(RUN, undefined, result), undefined)
  for (const status of ['queued', 'running', 'retrying', 'failed'] as const) {
    assert.equal(explicitHistoricalRun(RUN, job({ status }), result), undefined, status)
  }
  // The page shows nothing of this run (another field context, older boundary): nothing to ask about.
  assert.equal(explicitHistoricalRun(RUN, job(), undefined), undefined)
  assert.equal(explicitHistoricalRun(RUN, job(), { ...result }), undefined)
  // Demo data is not historical evidence.
  const demo = { ...result, demo: true }
  assert.equal(explicitHistoricalRun(RUN, job({ result: demo }), demo), undefined)
  const demoSource = { ...result, source: 'demo' } as WaterResult
  assert.equal(explicitHistoricalRun(RUN, job({ result: demoSource }), demoSource), undefined)
})

test('the run period shown to the farmer comes from the server-built historical context only', () => {
  const runContext = {
    available_charts: [{ chart_id: 'c_water_season', metric_ids: ['m_rootzone_reserve', 'm_precip'] }],
    timeseries_index: [{ metric_id: 'm_rootzone_reserve', range: { start: '2026-08-01', end: '2026-08-10' } }],
  }
  assert.deepEqual(historicalPeriod(runContext), { start: '2026-08-01', end: '2026-08-10' })
  const forecastContext = {
    available_charts: [{ chart_id: 'c_water_forecast', metric_ids: ['m_rootzone_reserve'] }],
    timeseries_index: runContext.timeseries_index,
  }
  assert.equal(historicalPeriod(forecastContext), undefined)
  assert.equal(historicalPeriod(null), undefined)
  assert.equal(historicalPeriod({ available_charts: runContext.available_charts, timeseries_index: [] }), undefined)
})
