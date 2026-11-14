import assert from 'node:assert/strict'
import test from 'node:test'
import type { Job, MoistureLayer } from '../src/api/contracts.ts'
import { isStaleMoisture, latestMoistureArchiveDate, visibleMoisture } from '../src/features/fields/moisture.ts'

const provenance = {
  name: 'Example weather dataset', provider: 'Example', url: 'https://example.com', resolution_degrees: 0.1,
  grid_latitude: 41.7, grid_longitude: -93.4, sample_count: 24, requested_latitude: 41.71, requested_longitude: -93.41,
}
const latest: MoistureLayer = {
  field_id: 'F-03', season: 2026, demo: false, date: '2026-08-20', depth_cm: [5, 5], unit: 'm³/m³',
  minimum: 0, maximum: 1, cells: [],
  source: { ...provenance, kind: 'ml', model_version: 'test-v1', generated_at: '2026-08-24T12:00:00Z' },
}
const older: MoistureLayer = { ...latest, date: '2026-08-10' }
const job: Job = {
  id: 'dated-moisture-run', kind: 'geo_ml', field_id: older.field_id, status: 'succeeded', attempts: 1,
  result: older, error: null, created_at: '2026-08-24T12:00:00Z', updated_at: '2026-08-24T12:01:00Z',
}

test('latest ML mode prefers a newer scheduled estimate over a manually run older date', () => {
  assert.equal(visibleMoisture(latest, job, 'F-03', 2026, 'ml', ''), latest)
  assert.equal(visibleMoisture(null, job, 'F-03', 2026, 'ml', ''), older)
})

test('a requested day only shows a result for that date', () => {
  assert.equal(visibleMoisture(latest, job, 'F-03', 2026, 'ml', older.date), older)
  assert.equal(visibleMoisture(latest, job, 'F-03', 2026, 'ml', '2026-08-15'), undefined)
  assert.equal(visibleMoisture(latest, job, 'F-03', 2026, 'ml', latest.date), latest)
  assert.equal(visibleMoisture(null, { ...job, status: 'retrying' }, 'F-03', 2026, 'ml', older.date), undefined)
})

test('switching source, field or season cannot mislabel an older response', () => {
  const historical: MoistureLayer = { ...latest, source: { ...provenance, kind: 'reanalysis' } }
  const demo: MoistureLayer = { ...latest, demo: true, source: null }
  assert.equal(visibleMoisture(historical, job, 'F-03', 2026, 'historical', ''), historical)
  assert.equal(visibleMoisture(demo, job, 'F-03', 2026, 'demo', ''), demo)
  assert.equal(visibleMoisture(demo, undefined, 'F-03', 2026, 'ml', ''), undefined)
  assert.equal(visibleMoisture(latest, job, 'F-02', 2026, 'ml', ''), undefined)
  assert.equal(visibleMoisture(latest, job, 'F-03', 2027, 'ml', ''), undefined)
})

test('latest freshness uses UTC calendar days and warns only after nine days', () => {
  assert.equal(isStaleMoisture('2026-08-20', new Date('2026-08-29T23:59:59Z')), false)
  assert.equal(isStaleMoisture('2026-08-20', new Date('2026-08-30T00:00:00Z')), true)
})

test('latest ML crosses calendar years while exact dates retain their season', () => {
  const nextYear: MoistureLayer = { ...latest, season: 2027, date: '2027-01-01' }
  assert.equal(visibleMoisture(nextYear, job, 'F-03', undefined, 'ml', ''), nextYear)
  assert.equal(visibleMoisture(nextYear, job, 'F-03', 2026, 'ml', older.date), older)
  assert.equal(latestMoistureArchiveDate(new Date('2027-01-03T01:00:00Z')), '2026-12-28')
})

test('latest ERA5-Land prefers a newer scheduled estimate across years', () => {
  const historical: MoistureLayer = { ...latest, source: { ...provenance, kind: 'reanalysis' } }
  const nextYear: MoistureLayer = { ...historical, season: 2027, date: '2027-01-01' }
  const oldJob = { ...job, result: historical }
  assert.equal(visibleMoisture(nextYear, oldJob, 'F-03', undefined, 'historical', ''), nextYear)
  assert.equal(visibleMoisture(nextYear, oldJob, 'F-03', 2026, 'historical', historical.date), historical)
})
