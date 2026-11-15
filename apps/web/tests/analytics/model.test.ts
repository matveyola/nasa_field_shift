import test from 'node:test'
import assert from 'node:assert/strict'
import type { Field, WaterForecast } from '../../src/api/contracts.ts'
import type { FieldProfileSnapshot } from '../../src/api/planningV1.generated.ts'
import type { VegetationResult } from '../../src/api/analytics.ts'
import { historyBeforeSeason, overviewInsights, profileForField, rotationEvidence, vegetationEvidence } from '../../src/features/analytics/model.ts'

const field = { id: 'field-1', boundary_revision: 2 } as Field
const profile = { field_id: field.id, geometry_revision: 2, land_status: 'arable', history: [], soil: [] } as unknown as FieldProfileSnapshot
const crop = (season: number, id: string | null, status: 'confirmed' | 'reported' | 'unknown' = 'confirmed') => ({ season, crop: id ? { crop_id: id, cultivar_id: null, profile_version: null } : null, status, sowing_date: null, harvest_date: null, source_ids: [] })
const observation = { date: '2026-10-15', scene_id: 'scene', mean_ndvi: .6, p10_ndvi: .4, p90_ndvi: .8, valid_percent: 85, pixel_count: 100, catalog_url: 'https://example.test' }
const vegetation = { field_id: field.id, boundary_revision: 2, season: 2026, observations: [observation] } as VegetationResult

test('field profiles are isolated by identity and current geometry', () => {
  assert.equal(profileForField([profile], field).status, 'available')
  assert.equal(profileForField([{ ...profile, field_id: 'another' }], field).status, 'missing')
  assert.equal(profileForField([{ ...profile, geometry_revision: 1 }], field).status, 'boundary_changed')
  assert.equal(profileForField(undefined, field).status, 'missing')
})
test('history excludes selected and future seasons and preserves unknown entries', () => {
  const p = { ...profile, history: [crop(2027, 'maize'), crop(2025, 'soy'), crop(2024, null, 'unknown'), crop(2026, 'wheat')] }
  assert.deepEqual(historyBeforeSeason(p, 2026).map(h => h.season), [2025, 2024])
  assert.equal(rotationEvidence(p, 2026).unknown.length, 1)
  assert.equal(p.history[0].season, 2027, 'does not mutate saved order')
})
test('a missing intervening year cannot establish continuous cropping', () => {
  assert.equal(rotationEvidence({ ...profile, history: [crop(2025, 'Maize'), crop(2023, 'maize')] }, 2026).repeated.length, 0)
  assert.equal(rotationEvidence({ ...profile, history: [crop(2025, 'Maize'), crop(2024, 'maize', 'reported')] }, 2026).repeated.length, 1)
  assert.equal(rotationEvidence({ ...profile, history: [crop(2025, 'Maize'), crop(2024, 'Maize', 'unknown')] }, 2026).repeated.length, 0)
})
test('satellite conclusions enforce the same usable-coverage policy as the chart', () => {
  assert.equal(vegetationEvidence(vegetation, field, 2026, '2026-11-15')?.ageDays, 31)
  for (const patch of [{ valid_percent: 69 }, { pixel_count: 9 }, { mean_ndvi: NaN }, { date: '2026-11-30' }, { date: '2026-02-31' }]) {
    assert.equal(vegetationEvidence({ ...vegetation, observations: [{ ...observation, ...patch }] }, field, 2026, '2026-11-15'), undefined)
  }
})
test('satellite data from a different field, season or boundary is not reused', () => {
  for (const patch of [{ field_id: 'another' }, { season: 2025 }, { boundary_revision: 1 }]) {
    assert.equal(vegetationEvidence({ ...vegetation, ...patch }, field, 2026, '2026-11-15'), undefined)
  }
  const past = { ...vegetation, season: 2025, observations: [{ ...observation, date: '2025-10-15' }] }
  assert.equal(vegetationEvidence(past, field, 2025, '2026-11-15')?.historical, true)
})
test('missing evidence yields explicit actions, never zero risk or healthy conditions', () => {
  const conclusions = overviewInsights(field, 2026, '2026-11-15', null, undefined, undefined)
  assert.deepEqual(new Set(conclusions.map(i => i.code)), new Set(['waterMissing', 'profileMissing', 'vegetationMissing', 'historyMissing']))
  assert.equal(conclusions[0].code, 'profileMissing')
})
test('a current threshold crossing leads, but it cannot describe a different selected season', () => {
  const forecast = { analysis: 'water_forecast', field_id: field.id, season: 2026, initialized_at: '2026-11-14T12:00:00Z', threshold_mm: 60,
    days: [{ date: '2026-11-15', reserve_mm: 30 }] } as WaterForecast
  assert.equal(overviewInsights(field, 2026, '2026-11-15', forecast, undefined, profile)[0].code, 'waterShortage')
  assert.equal(overviewInsights(field, 2025, '2026-11-15', forecast, undefined, profile).find(i => i.section === 'water')?.code, 'waterMissing')
})
test('partial or rejected soil parameters cannot imply sufficient water inputs', () => {
  const soil = { top_cm: 0, bottom_cm: 30, field_capacity_m3_m3: .3, wilting_point_m3_m3: .1, quality: 'rejected' }
  const p = { ...profile, soil: [soil] } as FieldProfileSnapshot
  assert.equal(overviewInsights(field, 2026, '2026-11-15', null, undefined, p).find(i => i.section === 'soil')?.code, 'soilMissing')
})
