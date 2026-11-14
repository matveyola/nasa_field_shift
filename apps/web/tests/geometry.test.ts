import assert from 'node:assert/strict'
import test from 'node:test'
import { basemaps, coordinateFromMap, hasUsgsAerial, placeLabels, preferredAerial } from '../src/features/fields/basemaps.ts'
import { moistureColor, parseVertices, polygonFrom } from '../src/features/fields/geometry.ts'

test('GeoJSON polygon closes once and preserves absolute longitude and latitude', () => {
  const ring = parseVertices('30.47,50.24\r\n30.48,50.24\n30.48,50.25\n30.47,50.24')
  const polygon = polygonFrom(ring)
  assert.equal(polygon.coordinates[0].length, 4)
  assert.deepEqual(polygon.coordinates[0][0], polygon.coordinates[0][3])
  assert.deepEqual(polygon.coordinates[0][1], [30.48, 50.24])
})

test('map clicks are longitude-first and local USGS coverage does not restrict the global basemap', () => {
  const coordinate = coordinateFromMap({ lat: 41.7, lng: -93.5 })
  assert.deepEqual(coordinate, [-93.5, 41.7])
  assert.equal(hasUsgsAerial(coordinate), true)
  assert.equal(hasUsgsAerial([30.47, 50.24]), false)
  assert.match(basemaps.aerial.url, /s2cloudless_3857\/default\/g\/\{z\}\/\{y\}\/\{x\}\.jpg$/)
  assert.match(basemaps.street.url, /\/\{z\}\/\{x\}\/\{y\}\.png$/)
})

test('imagery sources change with coverage and keep map labels above the field zoom', () => {
  assert.ok(placeLabels.maxZoom < 15)
  assert.equal(preferredAerial([1.786, 48.31], 6), undefined)
  assert.match(preferredAerial([1.786, 48.31], 16)?.url ?? '', /data\.geopf\.fr/)
  assert.match(preferredAerial([-4.25, 41.7], 16)?.url ?? '', /ign\.es/)
  assert.equal(preferredAerial([13.59, 51.31], 16)?.wmsLayers, 'sn_dop_020')
  assert.match(preferredAerial([-93.4561, 41.7269], 16)?.url ?? '', /nationalmap\.gov/)
  assert.equal(preferredAerial([34.557, 49.667], 16), undefined)
  assert.match(preferredAerial([34.557, 49.667], 16, 'a&b')?.url ?? '', /api\.maptiler\.com.*key=a%26b$/)
})

test('invalid or collinear coordinates fail before submission', () => {
  assert.throws(() => parseVertices('30.47,'))
  assert.throws(() => polygonFrom([[1, 1], [2, 2], [3, 3]]))
  assert.throws(() => polygonFrom([[1, 1], [2, 2], [190, 3]]))
})

test('moisture palette handles out-of-range and flat values', () => {
  assert.equal(moistureColor(-1, 0, 1), moistureColor(0, 0, 1))
  assert.equal(moistureColor(2, 0, 1), moistureColor(1, 0, 1))
  assert.match(moistureColor(.5, .5, .5), /^rgb\(\d+,\d+,\d+\)$/)
})
