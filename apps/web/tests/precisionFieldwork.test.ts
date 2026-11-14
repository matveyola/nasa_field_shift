import assert from 'node:assert/strict'
import test from 'node:test'
import { readGeoJsonBoundary } from '../src/features/fields/geoJsonBoundary.ts'
import { polygonFrom } from '../src/features/fields/geometry.ts'
import { readMeasurements, summarizeMeasurements } from '../src/features/fields/precision/measurements.ts'
import { compareHistory, inRing, samplingPoints, type SceneLayer, type ZoneCell } from '../src/features/fields/precision/zones.ts'

function rectangle(x: number, zone: ZoneCell['zone']): ZoneCell {
  return { zone, value: .5, geometry: polygonFrom([[x, 48], [x + .01, 48], [x + .01, 48.01], [x, 48.01]]) }
}

function layer(cells: ZoneCell[]): SceneLayer {
  return { date: '2026-07-01', cells, median_ndvi: .5, spread_ndvi: .2, low_area_percent: 10, valid_percent: 95, resolution_m: 20 }
}

test('GeoJSON import unwraps a single named field and refuses unsupported coordinate systems or holes', async () => {
  const geometry = rectangle(30, 'typical').geometry
  const feature = { type: 'Feature', properties: { name: ' West ' }, geometry }
  const file = (data: object) => new File([JSON.stringify(data)], 'field.geojson')
  const boundary = await readGeoJsonBoundary(file({ type: 'FeatureCollection', features: [feature] }))
  assert.equal(boundary.name, 'West')
  assert.deepEqual(polygonFrom(boundary.points), geometry)
  await assert.rejects(readGeoJsonBoundary(file({ ...geometry, crs: {} })), /WGS84/)
  await assert.rejects(readGeoJsonBoundary(file({ ...geometry, coordinates: [...geometry.coordinates, ...geometry.coordinates] })), /WGS84/)
  await assert.rejects(readGeoJsonBoundary(file({ type: 'FeatureCollection', features: [feature, feature] })), /one WGS84 polygon/)
})

test('history distinguishes stable cells, changed categories and cells hidden in another year', () => {
  const low = rectangle(30, 'low')
  const typical = rectangle(30.01, 'typical')
  const high = rectangle(30.02, 'high')
  const reversed = { ...low, geometry: { ...low.geometry, coordinates: [[...low.geometry.coordinates[0]].reverse()] } }
  const base = layer([low, typical, high])
  const prior = layer([reversed, { ...typical, zone: 'low' }])
  assert.equal(compareHistory([base, prior]), undefined)
  const result = compareHistory([base, prior, base])!
  assert.deepEqual(result.stableCells, [low])
  assert.ok(result.persistent > 0 && result.changing > 0 && result.unmatched > 0)
  assert.equal(result.typical, 0)
})

test('sampling points stay inside concave cells', () => {
  const geometry = polygonFrom([[30, 48], [30.03, 48], [30.03, 48.01], [30.01, 48.01], [30.01, 48.03], [30, 48.03]])
  const points = samplingPoints([{ zone: 'low', value: .25, geometry }])
  assert.equal(points.length, 1)
  assert.ok(inRing(points[0].position, geometry.coordinates[0]))
})

test('yield import preserves zero, excludes points outside and on ambiguous borders, and retains group counts', async () => {
  const file = new File([
    'longitude,latitude,yield_t_ha,group\n30.005,48.005,0,control\n30.005,48.005,6,variable\n30.01,48.005,20,variable\n31,48,30,control',
  ], 'yield.csv')
  const rows = await readMeasurements(file, 'yield')
  const result = summarizeMeasurements(rows, [rectangle(30, 'low'), rectangle(30.01, 'high')])
  assert.equal(result.excluded, 2)
  assert.deepEqual(result.rows[0], { zone: 'low', count: 2, value: 3, control: 0, variable: 6, controlCount: 1, variableCount: 1 })
  assert.equal(result.rows[1].value, null)
  await assert.rejects(readMeasurements(new File(['longitude,latitude,ph\n30,48,15'], 'soil.csv'), 'soil'), /invalid/)
})
