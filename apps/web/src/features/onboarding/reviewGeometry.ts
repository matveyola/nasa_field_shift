import type { Polygon, Position } from '../../api/contracts.ts'
import { estimateHectares, polygonFrom } from '../fields/geometry.ts'

function side(a: Position, b: Position, p: Position) {
  const cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
  return Math.abs(cross) < 1e-15 ? 0 : Math.sign(cross)
}
function onSegment(a: Position, b: Position, p: Position) {
  return Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0])
    && Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1])
}
function intersects(a: Position, b: Position, c: Position, d: Position) {
  const abc = side(a, b, c), abd = side(a, b, d), cda = side(c, d, a), cdb = side(c, d, b)
  return (abc * abd < 0 && cda * cdb < 0)
    || (abc === 0 && onSegment(a, b, c)) || (abd === 0 && onSegment(a, b, d))
    || (cda === 0 && onSegment(c, d, a)) || (cdb === 0 && onSegment(c, d, b))
}

/** Match the server's simple-ring requirements before advancing to the questionnaire. */
export function reviewedBoundary(points: Position[]): Polygon | null {
  try {
    const geometry = polygonFrom(points)
    const ring = geometry.coordinates[0]
    const size = ring.length - 1
    for (let i = 0; i < size; i++) {
      const previous = ring[(i + size - 1) % size], vertex = ring[i], next = ring[i + 1]
      if (side(previous, vertex, next) === 0 && !onSegment(previous, next, vertex)) return null
      for (let j = i + 2; j < size; j++) {
        if (i === 0 && j === size - 1) continue
        if (intersects(vertex, next, ring[j], ring[j + 1])) return null
      }
    }
    const area = estimateHectares(ring.slice(0, -1))
    return area > 0 && area <= 100_000 ? geometry : null
  } catch { return null }
}
