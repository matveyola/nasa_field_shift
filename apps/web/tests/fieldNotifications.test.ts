import assert from 'node:assert/strict'
import test from 'node:test'
import type { Field, WaterForecast } from '../src/api/contracts.ts'
import type { VegetationResult } from '../src/api/analytics.ts'
import { loadFieldNotifications, type NotificationSources } from '../src/features/notifications/fieldNotifications.ts'

const today = '2026-11-10'
function field(id = 'F1'): Field {
  return { id, name: `Field ${id}`, crop: 'Maize', area_ha: 10, boundary_revision: 2, planting: null,
    geometry: { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] } }
}
function forecast(id = 'F1', reserves = [80, 70, 60]): WaterForecast {
  return { analysis: 'water_forecast', field_id: id, season: 2026, crop: 'Maize', initialized_at: '2026-11-09T00:00:00Z',
    generated_at: '2026-11-09T06:00:00Z', method: 'fao56', model_version: null, physical_model_version: 'test', provider: 'ECMWF',
    source_url: 'https://www.ecmwf.int', grid_resolution_km: 9, root_depth_m: 1, initial_reserve_mm: 90, capacity_mm: 150,
    threshold_mm: 65, field_capacity: .3, wilting_point: .15, crop_coefficient: 1, limitations: [],
    days: reserves.map((reserve, index) => ({ date: `2026-11-${10 + index}`, reserve_mm: reserve, root_zone_estimate_mm: 0,
      opening_mm: 90, rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0, lead_days: index + 1 })) }
}
function vegetation(id = 'F1', lowArea = 15): VegetationResult {
  return { field_id: id, season: 2026, boundary_revision: 2, geometry_fingerprint: 'footprint', generated_at: '2026-11-09T12:00:00Z',
    status: 'ready', sampling: 'monthly_clear_scene', resolution_m: 20,
    observations: [{ date: '2026-11-08', scene_id: 'S2-scene', mean_ndvi: .6, p10_ndvi: .4, p90_ndvi: .8, valid_percent: 80,
      pixel_count: 100, catalog_url: 'https://example.org' }],
    heterogeneity: { date: '2026-11-08', median_ndvi: .65, spread_ndvi: .4, low_area_percent: lowArea,
      valid_percent: 80, resolution_m: 20, low_threshold: .5, high_threshold: .8, cells: [] },
    months: [], source_url: 'https://example.org', limitations: [] }
}
function sources(overrides: Partial<NotificationSources> = {}): NotificationSources {
  return { waterForecast: async (id) => forecast(id), vegetation: async (id) => vegetation(id), ...overrides }
}
function load(overrides: Partial<NotificationSources> = {}, season = 2026) {
  return loadFieldNotifications([field()], season, today, new AbortController().signal, sources(overrides))
}

test('real saved water and vegetation evidence produces field-bound notifications with provenance', async () => {
  const result = await load()
  assert.equal(result.unavailableFields, 0)
  assert.deepEqual(result.items.map((item) => [item.kind, item.fieldId, item.fieldName, item.boundaryRevision, item.date, item.source]),
    [['water', 'F1', 'Field F1', 2, '2026-11-12', 'FAO-56 · ECMWF'], ['vegetation', 'F1', 'Field F1', 2, '2026-11-08', 'Sentinel-2']])
  const water = result.items[0]
  assert.equal(water.kind, 'water')
  if (water.kind === 'water') {
    assert.equal(water.minimumMm, 60)
    assert.equal(water.thresholdMm, 65)
  }
})

test('clear water and NDVI below inspection threshold do not fabricate notifications', async () => {
  const result = await load({ waterForecast: async () => forecast('F1', [80, 70, 65]), vegetation: async () => vegetation('F1', 9.9) })
  assert.deepEqual(result, { items: [], unavailableFields: 0 })
})

test('missing or failed sources remain unknown and never become a water-risk alert', async () => {
  assert.deepEqual(await load({ waterForecast: async () => null, vegetation: async () => { throw new Error('offline') } }),
    { items: [], unavailableFields: 1 })
  const partial = await load({ waterForecast: async () => { throw new Error('offline') } })
  assert.equal(partial.unavailableFields, 1)
  assert.deepEqual(partial.items.map((item) => item.kind), ['vegetation'])
})

test('water identity, revision, season, freshness, demo and invalid reserves cannot generate alerts', async () => {
  const cases = [
    { ...forecast(), field_id: 'other' }, { ...forecast(), boundary_revision: 1 },
    { ...forecast(), season: 2025 }, { ...forecast(), demo: true },
    { ...forecast(), initialized_at: '2026-11-06T00:00:00Z' },
    { ...forecast(), initialized_at: '2026-11-11T00:00:00Z' },
    { ...forecast(), days: forecast().days.slice(1) }, forecast('F1', [80, NaN, 60]),
  ]
  for (const input of cases) {
    const result = await load({ waterForecast: async () => input })
    assert.deepEqual(result.items.map((item) => item.kind), ['vegetation'])
    assert.equal(result.unavailableFields, 1)
  }
})

test('vegetation must match field, revision, season, current valid observations and real source metadata', async () => {
  const base = vegetation()
  const cases: VegetationResult[] = [
    { ...base, field_id: 'other' }, { ...base, boundary_revision: 1 }, { ...base, season: 2025 },
    { ...base, demo: true } as VegetationResult, { ...base, status: 'unavailable' }, { ...base, generated_at: null },
    { ...base, generated_at: '2026-11-11T00:00:00Z' }, { ...base, geometry_fingerprint: '' },
    { ...base, heterogeneity: { ...base.heterogeneity!, valid_percent: 69 } },
    { ...base, observations: [{ ...base.observations[0], pixel_count: 9 }] },
    { ...base, observations: [{ ...base.observations[0], date: '2026-11-11' }], heterogeneity: { ...base.heterogeneity!, date: '2026-11-11' } },
    { ...base, observations: [{ ...base.observations[0], date: '2026-10-01' }], heterogeneity: { ...base.heterogeneity!, date: '2026-10-01' } },
  ]
  for (const input of cases) {
    const result = await load({ vegetation: async () => input })
    assert.deepEqual(result.items.map((item) => item.kind), ['water'])
    assert.equal(result.unavailableFields, 1)
  }
})

test('repeat polls and a new forecast cycle preserve the event ID; changed boundary or footprint creates a new one', async () => {
  const original = await load()
  const repeated = await load({ waterForecast: async () => ({ ...forecast(), initialized_at: '2026-11-10T00:00:00Z' }) })
  assert.deepEqual(original.items.map((item) => item.id), repeated.items.map((item) => item.id))
  const revision = await loadFieldNotifications([{ ...field(), boundary_revision: 3 }], 2026, today, new AbortController().signal,
    sources({ vegetation: async () => ({ ...vegetation(), boundary_revision: 3 }) }))
  assert.notEqual(original.items[0].id, revision.items[0].id)
  const footprint = await load({ vegetation: async () => ({ ...vegetation(), geometry_fingerprint: 'new-planting' }) })
  assert.notEqual(original.items[1].id, footprint.items[1].id)
})

test('historical or invalid date contexts make no requests', async () => {
  let requests = 0
  const source = sources({ waterForecast: async () => { requests++; return forecast() } })
  assert.deepEqual(await loadFieldNotifications([field()], 2025, today, new AbortController().signal, source), { items: [], unavailableFields: 1 })
  assert.deepEqual(await loadFieldNotifications([field()], 2026, '2026-02-30', new AbortController().signal, source), { items: [], unavailableFields: 1 })
  assert.equal(requests, 0)
})

test('combined requests across both sources stay bounded at three and result order is deterministic', async () => {
  let active = 0, maximum = 0
  async function wait() {
    active++; maximum = Math.max(active, maximum)
    await new Promise((resolve) => setTimeout(resolve, 2))
    active--
  }
  const fields = Array.from({ length: 8 }, (_, index) => field('F' + index))
  const result = await loadFieldNotifications(fields, 2026, today, new AbortController().signal, {
    waterForecast: async (id) => { await wait(); return forecast(id) },
    vegetation: async (id) => { await wait(); return vegetation(id) },
  })
  assert.equal(maximum, 3)
  assert.equal(result.items.length, 16)
  assert.deepEqual(result.items.slice(0, 8).map((item) => item.fieldId), fields.map((item) => item.id))
  assert.ok(result.items.slice(0, 8).every((item) => item.kind === 'water'))
})

test('abort stops dequeuing or fetching second sources and never returns a partial successful snapshot', async () => {
  const controller = new AbortController()
  let started = 0
  await assert.rejects(loadFieldNotifications(Array.from({ length: 10 }, (_, index) => field('F' + index)), 2026, today, controller.signal,
    sources({ waterForecast: async () => { started++; controller.abort(); return forecast() }, vegetation: async () => {
      assert.fail('No second source may begin after cancellation')
    } })), { name: 'AbortError' })
  assert.equal(started, 1)
  await assert.rejects(loadFieldNotifications([], 2026, today, controller.signal, sources()), { name: 'AbortError' })
})

test('an empty workspace performs no requests', async () => {
  assert.deepEqual(await loadFieldNotifications([], 2026, today, new AbortController().signal,
    sources({ waterForecast: async () => { assert.fail('Unexpected request') } })), { items: [], unavailableFields: 0 })
})
