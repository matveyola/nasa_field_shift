import assert from 'node:assert/strict'
import test from 'node:test'
import { segmentField, type FieldSegmentationInput } from '../src/features/fields/segmentationEngine.ts'

type Point = [number, number]
const width = 176
const height = 144

function photo(field: (x: number, y: number) => boolean, available = (_x: number, _y: number) => true) {
  const pixels = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4
      const rows = (x % 7 < 3 ? 3 : 0) + (y % 5)
      pixels.set(field(x, y) ? [96 + rows, 136 + rows, 72 + rows, 255]
        : [170 - rows, 155 - rows, 110 - rows, 255], offset)
      if (!available(x, y)) pixels.fill(0, offset, offset + 4)
    }
  }
  return pixels
}

function inside(ring: Point[], x: number, y: number) {
  let contains = false
  for (let index = 0; index < ring.length; index += 1) {
    const [ax, ay] = ring[index]
    const [bx, by] = ring[(index + 1) % ring.length]
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) contains = !contains
  }
  return contains
}

test('the clicked textured field retains its concavity and excludes a disconnected matching crop', () => {
  const clicked = (x: number, y: number) => x >= 22 && x < 106 && y >= 19 && y < 112 && (x < 64 || y < 62)
  const other = (x: number, y: number) => x >= 121 && x < 163 && y >= 25 && y < 116
  const pixels = photo((x, y) => clicked(x, y) || other(x, y))
  const input: FieldSegmentationInput = { width, height, pixels, seed: [43.2, 83.7] }
  const ring = segmentField(input)
  assert.ok(ring && ring.length >= 5 && ring.length <= 127)
  assert.equal(new Set(ring.map(String)).size, ring.length)
  assert.ok(inside(ring, ...input.seed))
  assert.ok(!inside(ring, 92, 100), 'the empty notch is outside the outline')
  assert.ok(!inside(ring, 143, 76), 'the matching stand across the gap is outside the outline')
  let intersection = 0
  let union = 0
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const actual = inside(ring, x + .5, y + .5)
      const expected = clicked(x, y)
      if (actual && expected) intersection += 1
      if (actual || expected) union += 1
    }
  }
  assert.ok(intersection / union > .85, `unexpected outline overlap: ${intersection / union}`)
  assert.deepEqual(segmentField(input), ring)
})

test('a cut-off, transparent, uniform or out-of-window region does not yield a contour', () => {
  const cropped = photo((x, y) => x < 104 && y >= 24 && y < 121)
  assert.equal(segmentField({ width, height, pixels: cropped, seed: [52, 67] }), null)

  const clippedByNoData = photo((x, y) => x >= 32 && x < 139 && y >= 23 && y < 114, (x) => x < 101)
  assert.equal(segmentField({ width, height, pixels: clippedByNoData, seed: [67, 71] }), null)
  assert.equal(segmentField({ width, height, pixels: clippedByNoData, seed: [116, 71] }), null)

  const uniform = new Uint8ClampedArray(width * height * 4).fill(255)
  assert.equal(segmentField({ width, height, pixels: uniform, seed: [50, 40] }), null)
  assert.equal(segmentField({ width, height, pixels: uniform, seed: [-1, 40] }), null)
  assert.equal(segmentField({ width, height, pixels: uniform.subarray(1), seed: [50, 40] }), null)
})

for (const scenario of [
  { name: 'a weak shared crop edge', trackWidth: 0, neighborShift: 9 },
  { name: 'an interrupted two-pixel track', trackWidth: 2, neighborShift: 0 },
]) {
  test(`Sentinel segmentation stops at ${scenario.name}`, () => {
    const width = 240
    const height = 240
    const pixels = new Uint8ClampedArray(width * height * 4)
    const target = (x: number, y: number) => x >= 28 && x < 113 && y >= 36 && y < 179
    const neighbor = (x: number, y: number) => x >= 113 + scenario.trackWidth && x < 205 && y >= 36 && y < 179
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const track = x >= 113 && x < 113 + scenario.trackWidth && y >= 36 && y < 179
        const noise = (x % 7 < 3 ? 3 : 0) + y % 5
        let rgb = [170, 153, 114]
        if (target(x, y) || neighbor(x, y) || track) {
          const contrast = track ? (y >= 95 && y < 103 ? 0 : 20)
            : neighbor(x, y) ? scenario.neighborShift : 0
          rgb = [102 + contrast, 130 + contrast, 81 + contrast]
        }
        pixels.set([...rgb.map((channel) => channel + noise), 255], 4 * (y * width + x))
      }
    }

    const ring = segmentField({ width, height, pixels, seed: [68.3, 111.7], source: 'satellite' })
    assert.ok(ring, 'the target has a usable visible contour')
    let intersection = 0
    let union = 0
    let includedNeighbor = 0
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const included = inside(ring, x + .5, y + .5)
        if (included && target(x, y)) intersection += 1
        if (included || target(x, y)) union += 1
        if (included && neighbor(x, y)) includedNeighbor += 1
      }
    }
    assert.ok(intersection / union > .9, `unexpected target overlap: ${intersection / union}`)
    assert.equal(includedNeighbor, 0, 'the outline must exclude the neighboring parcel')
  })
}

test('an aligned second Sentinel date separates crops that look alike on the anchor date', () => {
  const width = 240
  const height = 240
  const size = width * height
  const pixels = new Uint8ClampedArray(4 * size)
  const target = (x: number, y: number) => x >= 32 && x < 114 && y >= 37 && y < 176
  const neighbor = (x: number, y: number) => x >= 114 && x < 204 && y >= 37 && y < 176
  const anchor = { nir: new Float32Array(size), ndvi: new Float32Array(size) }
  const later = { nir: new Float32Array(size), ndvi: new Float32Array(size) }
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x
      const crop = target(x, y) || neighbor(x, y)
      const brightness = x % 3 + y % 4
      pixels.set(crop ? [104 + brightness, 129 + brightness, 88 + brightness, 255]
        : [169, 151, 114, 255], 4 * i)
      anchor.nir[i] = crop ? .44 : .23
      anchor.ndvi[i] = crop ? .59 : .14
      later.nir[i] = target(x, y) ? .38 : neighbor(x, y) ? .57 : .22
      later.ndvi[i] = target(x, y) ? .52 : neighbor(x, y) ? .73 : .13
    }
  }

  const input: FieldSegmentationInput = { width, height, pixels, seed: [68.4, 109.4], source: 'satellite' }
  const sameDate = segmentField({ ...input, spectralFrames: [anchor] })
  assert.ok(sameDate && inside(sameDate, 160, 100), 'the shared boundary has no spectral contrast on the anchor date')

  const ring = segmentField({ ...input, spectralFrames: [anchor, later] })
  assert.ok(ring && inside(ring, ...input.seed))
  let intersection = 0
  let union = 0
  let includedNeighbor = 0
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const included = inside(ring, x + .5, y + .5)
      if (included && target(x, y)) intersection += 1
      if (included || target(x, y)) union += 1
      if (included && neighbor(x, y)) includedNeighbor += 1
    }
  }
  assert.ok(intersection / union > .9, `unexpected target overlap: ${intersection / union}`)
  assert.equal(includedNeighbor, 0)
})

test('missing or misaligned spectral data preserves the RGB result', () => {
  const pixels = photo((x, y) => x >= 32 && x < 132 && y >= 26 && y < 116)
  const input: FieldSegmentationInput = { width, height, pixels, seed: [57, 61], source: 'satellite' }
  const missing = new Float32Array(width * height).fill(NaN)
  const baseline = segmentField(input)
  assert.ok(baseline)
  assert.deepEqual(segmentField({
    ...input,
    spectralFrames: [{ nir: missing, ndvi: missing }, { nir: missing.subarray(1), ndvi: missing }],
  }), baseline)
})
