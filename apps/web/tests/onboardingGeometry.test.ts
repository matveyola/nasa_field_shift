import assert from 'node:assert/strict'
import test from 'node:test'
import { reviewedBoundary } from '../src/features/onboarding/reviewGeometry.ts'
import type { Position } from '../src/api/contracts.ts'

test('review accepts corrected concave fields and closes an open outline once', () => {
  const points: Position[] = [[30, 50], [30.01, 50], [30.01, 50.01], [30.005, 50.005], [30, 50.01]]
  const boundary = reviewedBoundary(points)
  assert.ok(boundary)
  assert.deepEqual(boundary.coordinates[0], [...points, points[0]])
  assert.deepEqual(reviewedBoundary(boundary.coordinates[0]), boundary)
})

test('review rejects crossing and backtracking edges before saving an onboarding field', () => {
  // This crossing ring has nonzero signed area; an area-only check would accept it.
  assert.equal(reviewedBoundary([[30, 50], [30.012, 50.01], [30.01, 50], [30, 50.02]]), null)
  assert.equal(reviewedBoundary([[30, 50], [30.02, 50], [30.01, 50], [30.01, 50.01], [30, 50.01]]), null)
})

test('review keeps incomplete, repeated, invalid and oversized outlines unconfirmed', () => {
  assert.equal(reviewedBoundary([[30, 50], [30.01, 50]]), null)
  assert.equal(reviewedBoundary([[30, 50], [30.01, 50], [30, 50], [30, 50.01]]), null)
  assert.equal(reviewedBoundary([[NaN, 50], [30.01, 50], [30, 50.01]]), null)
  assert.equal(reviewedBoundary([[30, 50], [32, 50], [32, 52], [30, 52]]), null)
})
