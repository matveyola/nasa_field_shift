import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { VegetationResult } from '../src/api/analytics.ts'
import { adjacentObservationGroups, usableObservations, vegetationInsight } from '../src/features/fields/vegetationInsights.ts'
import { rankCombinedPriorities, loadFarmVegetation } from '../src/features/farm/farmVegetation.ts'
import type { Field } from '../src/api/contracts.ts'

const observation = (date: string) => ({ date, scene_id: date, mean_ndvi: .6, p10_ndvi: .4, p90_ndvi: .8, valid_percent: 80, pixel_count: 100, catalog_url: 'https://example.org' })
function result(): VegetationResult {
  return { field_id: 'a', season: 2026, boundary_revision: 2, geometry_fingerprint: 'hash', generated_at: null, status: 'ready', sampling: 'monthly_clear_scene', resolution_m: 20,
    observations: [observation('2026-07-03'), observation('2026-08-10'), observation('2026-10-20')],
    heterogeneity: { date: '2026-10-20', median_ndvi: .65, spread_ndvi: .4, low_area_percent: 15, valid_percent: 80, resolution_m: 20, low_threshold: .5, high_threshold: .8, cells: [] },
    months: [], source_url: 'https://example.org', limitations: [] }
}
test('seasonal series rejects inadequate coverage, too few pixels, invalid values and another season', () => {
  const data = result()
  data.observations.push({ ...observation('2026-05-01'), valid_percent: 69.9 }, { ...observation('2026-04-01'), pixel_count: 9 },
    { ...observation('2026-03-01'), mean_ndvi: Number.NaN }, observation('2025-07-01'))
  assert.deepEqual(usableObservations(data).map((point) => point.date), ['2026-07-03', '2026-08-10', '2026-10-20'])
})
test('chart splits missing calendar months instead of interpolating gaps', () => {
  assert.deepEqual(adjacentObservationGroups(usableObservations(result())).map((group) => group.length), [2, 1])
})
test('inspection cue uses observed-area threshold and becomes historical after 30 days', () => {
  const data = result()
  assert.equal(vegetationInsight(data, '2026-11-10')?.inspect, true)
  assert.equal(vegetationInsight(data, '2026-11-20')?.inspect, false)
  assert.equal(vegetationInsight(data, '2026-11-20')?.stale, true)
  data.heterogeneity!.low_area_percent = 0
  assert.equal(vegetationInsight(data, '2026-11-10')?.lowAreaPercent, 0)
  assert.equal(vegetationInsight(data, '2026-11-10')?.inspect, false)
})
test('future observations, mismatched map dates and impossible shares do not create signals', () => {
  const data = result()
  assert.equal(vegetationInsight(data, '2026-10-19'), undefined)
  data.heterogeneity!.date = '2026-09-20'
  assert.equal(vegetationInsight(data, '2026-11-10'), undefined)
  data.heterogeneity!.date = '2026-10-20'
  data.heterogeneity!.low_area_percent = 101
  assert.equal(vegetationInsight(data, '2026-11-10'), undefined)
})
test('farm ranks actual shortage first, then vegetation inspection, then missing data and clear fields', () => {
  const base = { fieldId: '', minimumMm: 60, thresholdMm: 40, marginMm: 20, firstStressDate: null, daysToStress: null, startDate: '2026-11-10', endDate: '2026-11-13', initializedAt: '2026-11-09T00:00:00Z', values: [60] }
  const entries = [
    { field: { id: 'clear' } as Field, priority: { ...base, kind: 'clear' as const, fieldId: 'clear' } },
    { field: { id: 'inspect' } as Field, priority: { ...base, kind: 'clear' as const, fieldId: 'inspect' } },
    { field: { id: 'unknown' } as Field, priority: { kind: 'unavailable' as const, fieldId: 'unknown', reason: 'missing' as const } },
    { field: { id: 'dry' } as Field, priority: { ...base, kind: 'shortage' as const, fieldId: 'dry', minimumMm: 30, marginMm: -10, firstStressDate: '2026-11-11', daysToStress: 1 } },
  ]
  assert.deepEqual(rankCombinedPriorities(entries, { inspect: vegetationInsight(result(), '2026-11-10') }).map(({ field }) => field.id), ['dry', 'inspect', 'unknown', 'clear'])
  assert.equal(entries[0].field.id, 'clear')
})
test('farm satellite loader rejects field/revision mismatch and isolates missing sources', async () => {
  const fields = ['a', 'b', 'c'].map((id) => ({ id, boundary_revision: 2 }) as Field)
  const output = await loadFarmVegetation(fields, 2026, '2026-11-10', new AbortController().signal, async (id) => {
    if (id === 'c') throw new Error('Source unavailable')
    return { ...result(), field_id: id, boundary_revision: id === 'b' ? 1 : 2 }
  })
  assert.equal(output.a?.inspect, true)
  assert.equal(output.b, undefined)
  assert.equal(output.c, undefined)
})
