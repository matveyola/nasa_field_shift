import assert from 'node:assert/strict'
import test from 'node:test'
import { basemaps, coordinateFromMap, defaultBasemap } from '../src/features/fields/basemaps.ts'
import { moistureColor, parseVertices, polygonFrom } from '../src/features/fields/geometry.ts'

test('GeoJSON polygon closes once and preserves absolute longitude and latitude', () => {
  const ring = parseVertices('30.47,50.24\r\n30.48,50.24\n30.48,50.25\n30.47,50.24')
  const polygon = polygonFrom(ring)
  assert.equal(polygon.coordinates[0].length, 4)
  assert.deepEqual(polygon.coordinates[0][0], polygon.coordinates[0][3])
  assert.deepEqual(polygon.coordinates[0][1], [30.48, 50.24])
})

test('map clicks become longitude-first GeoJSON and choose a covered provider', () => {
  const coordinate = coordinateFromMap({ lat: 41.7, lng: -93.5 })
  assert.deepEqual(coordinate, [-93.5, 41.7])
  assert.equal(defaultBasemap(coordinate), 'aerial')
  assert.equal(defaultBasemap([30.47, 50.24]), 'street')
  assert.equal(defaultBasemap(), 'street')
  assert.match(basemaps.aerial.url, /\/tile\/\{z\}\/\{y\}\/\{x\}$/)
  assert.match(basemaps.street.url, /\/\{z\}\/\{x\}\/\{y\}\.png$/)
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
