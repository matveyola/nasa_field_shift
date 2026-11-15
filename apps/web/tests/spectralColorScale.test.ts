import assert from 'node:assert/strict'
import test from 'node:test'
import { spectralColorStops } from '../src/features/fields/spectralIndices.ts'
import { fieldColorScale } from '../src/features/fields/fieldRasterRenderer.ts'

test('standard NDVI preserves a fixed range while field contrast exposes the measured range', () => {
  const standard = spectralColorStops('ndvi')
  const values = [.42, .51, .58, NaN, Infinity, -2]
  const contrasted = spectralColorStops('ndvi', values)
  assert.equal(standard[0].value, -1)
  assert.equal(standard.at(-1)!.value, 1)
  assert.equal(contrasted[0].value, .42)
  assert.equal(contrasted.at(-1)!.value, .58)
  assert.notDeepEqual(fieldColorScale(standard)(.51), fieldColorScale(contrasted)(.51))
  assert.deepEqual(values, [.42, .51, .58, NaN, Infinity, -2])
})

test('flat observations produce a single color and unavailable values keep the standard scale', () => {
  const flat = spectralColorStops('ndvi', [.55, .55])
  assert.equal(flat.length, 1)
  assert.equal(flat[0].value, .55)
  assert.ok(fieldColorScale(flat)(.55).every(Number.isFinite))
  assert.deepEqual(spectralColorStops('ndvi', [NaN, Infinity, -2]), spectralColorStops('ndvi'))
  assert.deepEqual(spectralColorStops('ndvi', []), spectralColorStops('ndvi'))
})

test('moisture indices have a distinct palette and retain their signed physical index range', () => {
  for (const index of ['ndmi', 'ndwi'] as const) {
    const stops = spectralColorStops(index)
    assert.equal(stops[0].value, -1)
    assert.equal(stops.at(-1)!.value, 1)
    assert.notEqual(stops.at(-1)!.color, spectralColorStops('ndvi').at(-1)!.color)
    const contrast = spectralColorStops(index, [-.2, .4])
    assert.equal(contrast[0].value, -.2)
    assert.ok(Math.abs(contrast.at(-1)!.value - .4) < 1e-12)
  }
})

test('field contrast includes RECI and MCARI observations beyond the standard display range', () => {
  for (const [index, values] of [['reci', [2, 13, 20]], ['mcari', [.5, 3, 8]]] as const) {
    const stops = spectralColorStops(index, values)
    assert.equal(stops[0].value, values[0])
    assert.equal(stops.at(-1)!.value, values.at(-1))
    assert.notDeepEqual(fieldColorScale(stops)(values[0]), fieldColorScale(stops)(values[1]))
  }
})
