import assert from 'node:assert/strict'
import test from 'node:test'
import type { Polygon, Position } from '../src/api/contracts.ts'
import { buildFieldRaster, fieldColorScale, type RasterGeometry } from '../src/features/fields/fieldRasterRenderer.ts'

const rectangle = (left: number, top: number, right: number, bottom: number): Polygon => ({
  type: 'Polygon', coordinates: [[[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]],
})
const project = (point: Position) => point
const color = fieldColorScale([{ value: 0, color: '#000000' }, { value: 1, color: '#ffffff' }])

function pixel(raster: NonNullable<ReturnType<typeof buildFieldRaster>>, x: number, y: number) {
  const [left, top, right, bottom] = raster.bounds
  const column = Math.floor((x - left) * raster.width / (right - left))
  const row = Math.floor((y - top) * raster.height / (bottom - top))
  const offset = (row * raster.width + column) * 4
  return Array.from(raster.data.slice(offset, offset + 4))
}

test('adjacent continuous samples form a seamless transition without changing inputs', () => {
  const geometry = rectangle(0, 0, 3, 1)
  const cells = [0, 1, 1].map((value, index) => ({ geometry: rectangle(index, 0, index + 1, 1), value }))
  const source = structuredClone({ geometry, cells })
  const raster = buildFieldRaster({ geometry, cells, project, color })
  assert.ok(raster)
  assert.deepEqual(pixel(raster, .25, .5), [0, 0, 0, 255])
  const [dark, , , leftAlpha] = pixel(raster, .9, .5)
  const [light, , , rightAlpha] = pixel(raster, 1.1, .5)
  assert.ok(dark > 0 && dark < light && light < 255)
  assert.equal(leftAlpha, 255)
  assert.equal(rightAlpha, 255)
  assert.deepEqual(pixel(raster, 2.5, .5), [255, 255, 255, 255])
  assert.deepEqual({ geometry, cells }, source)
})

test('missing or invalid samples stay transparent and prevent interpolation across the gap', () => {
  const geometry = rectangle(0, 0, 3, 1)
  const cells = [0, NaN, 1].map((value, index) => ({ geometry: rectangle(index, 0, index + 1, 1), value }))
  const raster = buildFieldRaster({ geometry, cells, project, color })
  assert.ok(raster)
  assert.deepEqual(pixel(raster, .99, .5), [0, 0, 0, 255])
  assert.deepEqual(pixel(raster, 1.5, .5), [0, 0, 0, 0])
  assert.deepEqual(pixel(raster, 2.01, .5), [255, 255, 255, 255])
  assert.equal(buildFieldRaster({ geometry, cells: [cells[1]], project, color }), undefined)
})

test('the mask clips diagonal and concave edges and preserves holes and separate components', () => {
  const geometry: RasterGeometry = { type: 'MultiPolygon', coordinates: [
    [
      [[0, 0], [2, 0], [1.5, .7], [2, 2], [0, 2], [0, 0]],
      rectangle(.25, .25, .65, .75).coordinates[0],
    ],
    rectangle(2.5, .1, 2.6, 1.9).coordinates,
  ] }
  const cells = [{ geometry: rectangle(-1, -1, 4, 3), value: 1 }]
  const raster = buildFieldRaster({ geometry, cells, project, color })
  assert.ok(raster)
  assert.deepEqual(raster.bounds, [0, 0, 2.6, 2])
  assert.equal(pixel(raster, .5, .5)[3], 0)
  assert.equal(pixel(raster, 1.3, .75)[3], 255)
  assert.equal(pixel(raster, 1.75, .75)[3], 0)
  assert.equal(pixel(raster, 2.2, .75)[3], 0)
  assert.equal(pixel(raster, 2.55, .75)[3], 255)
  assert.ok(raster.data.some((value, index) => index % 4 === 3 && value > 0 && value < 255))
})

test('categorical zones use only their assigned colors even at shared or masked edges', () => {
  const geometry: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [2, 0], [1.6, 1], [0, 1], [0, 0]]] }
  const cells = [0, 1].map((value) => ({ geometry: rectangle(value, 0, value + 1, 1), value }))
  const raster = buildFieldRaster({ geometry, cells, project, color, categorical: true })
  assert.ok(raster)
  assert.deepEqual(pixel(raster, .99, .5), [0, 0, 0, 255])
  assert.deepEqual(pixel(raster, 1.01, .5), [255, 255, 255, 255])
  assert.ok(raster.data.every((value, index) => index % 4 === 3 || value === 0 || value === 255))
})

test('color stops preserve anchor colors and clamp values outside the legend', () => {
  assert.deepEqual(color(-1), [0, 0, 0])
  assert.deepEqual(color(.5), [127.5, 127.5, 127.5])
  assert.deepEqual(color(1), [255, 255, 255])
  assert.deepEqual(color(2), [255, 255, 255])
})
