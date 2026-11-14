import assert from 'node:assert/strict'
import test from 'node:test'
import { rasterizeRing, scoreProposal, summarize } from './evaluate.mjs'

const size = 32
const seed = [8, 8]
const rectangle = (west, south, east, north) => [[west, south], [east, south], [east, north], [west, north]]
const target = rectangle(4, 4, 14, 14)
const reference = rasterizeRing(target, size, size)
const neighbors = [{ id: 'next', ...rasterizeRing(rectangle(14, 4, 24, 14), size, size) }]

test('polygon metrics distinguish an edge shift from a merge with an adjacent field', () => {
  assert.equal(reference.area, 100)
  const exact = scoreProposal(target, reference, neighbors, size, size, seed)
  assert.equal(exact.iou, 1)
  assert.equal(exact.boundaryF1, 1)
  assert.equal(exact.leak, 0)
  assert.equal(exact.merged, false)

  const shift = scoreProposal(rectangle(5, 4, 15, 14), reference, neighbors, size, size, seed)
  assert.equal(shift.iou, 90 / 110)
  assert.equal(shift.boundaryF1, 1)
  assert.equal(shift.leak, 0.1)
  assert.equal(shift.highLeak, false)
  assert.equal(shift.neighboringOverlap[0].pixels, 10)
  assert.equal(shift.neighboringOverlap[0].pastBoundaryTolerance, 0)
  assert.equal(shift.merged, false)

  const merge = scoreProposal(rectangle(4, 4, 24, 14), reference, neighbors, size, size, seed)
  assert.equal(merge.iou, 0.5)
  assert.equal(merge.recall, 1)
  assert.ok(merge.boundaryF1 < 0.9)
  assert.equal(merge.leak, 0.5)
  assert.equal(merge.highLeak, true)
  assert.deepEqual(merge.neighboringOverlap, [{
    id: 'next', pixels: 100, shareOfProposal: 0.5, shareOfNeighbor: 1, pastBoundaryTolerance: 80, merge: true,
  }])
  assert.equal(merge.merged, true)
})

test('a missing proposal lowers effective IoU and coverage without counting as leakage', () => {
  const accepted = scoreProposal(target, reference, neighbors, size, size, seed)
  const rejected = scoreProposal(null, reference, neighbors, size, size, seed)
  const invalid = scoreProposal(rectangle(14, 4, 24, 14), reference, neighbors, size, size, seed)
  const summary = summarize([accepted, rejected, invalid].map((row) => ({
    ...row, neighborCount: neighbors.length, durationMs: 1,
  })))

  assert.equal(summary.coverage, 1 / 3)
  assert.equal(summary.meanIou, 1 / 3)
  assert.equal(summary.meanBoundaryF1, 1 / 3)
  assert.equal(summary.meanAcceptedIou, 1)
  assert.equal(summary.rejected, 1)
  assert.equal(summary.invalid, 1)
  assert.equal(summary.highLeak, 0)
  assert.equal(summary.merged, 0)
})
