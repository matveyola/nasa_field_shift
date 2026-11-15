import type { VegetationResult } from '../../../api/analytics'
import type { Polygon, Position } from '../../../api/contracts'
import { estimateHectares } from '../geometry.ts'

export type SceneLayer = NonNullable<VegetationResult['heterogeneity']>
export type ZoneCell = SceneLayer['cells'][number]
export type Zone = ZoneCell['zone']
export const zones: readonly Zone[] = ['low', 'typical', 'high']
export const zoneLabels: Record<Zone, string> = { low: 'Lower', typical: 'Typical', high: 'Higher' }
export const zoneColors: Record<Zone, string> = { low: '#e89836', typical: '#dbd65b', high: '#278b50' }

export function cellArea(cell: ZoneCell) {
  return estimateHectares(cell.geometry.coordinates[0])
}

export function zoneAreas(cells: ZoneCell[]) {
  const areas: Record<Zone, number> = { low: 0, typical: 0, high: 0 }
  for (const cell of cells) areas[cell.zone] += cellArea(cell)
  return areas
}

function horizontalCrossings(ring: Position[], latitude: number) {
  const crossings: number[] = []
  for (let index = 1; index < ring.length; index++) {
    const [x1, y1] = ring[index - 1]
    const [x2, y2] = ring[index]
    if ((y1 > latitude) !== (y2 > latitude)) crossings.push(x1 + (x2 - x1) * (latitude - y1) / (y2 - y1))
  }
  return crossings
}

export function inRing([longitude, latitude]: Position, ring: Position[]) {
  let inside = false
  for (let index = 1; index < ring.length; index++) {
    const [x1, y1] = ring[index - 1]
    const [x2, y2] = ring[index]
    const dx = x2 - x1
    const dy = y2 - y1
    if (dx === 0 && dy === 0) continue
    if (Math.abs(dx * (latitude - y1) - dy * (longitude - x1)) <= 1e-10 * Math.hypot(dx, dy)
      && longitude >= Math.min(x1, x2) && longitude <= Math.max(x1, x2)
      && latitude >= Math.min(y1, y2) && latitude <= Math.max(y1, y2)) return true
    if ((y1 > latitude) !== (y2 > latitude) && longitude < x1 + dx * (latitude - y1) / dy) inside = !inside
  }
  return inside
}

function interiorPoint(geometry: Polygon): Position | undefined {
  const ring = geometry.coordinates[0]
  const levels = [...new Set(ring.map(([, latitude]) => latitude))].sort((a, b) => a - b)
  let widest = 0
  let point: Position | undefined
  // Midpoints between vertex latitudes avoid ambiguous crossings at the vertices.
  for (let index = 1; index < levels.length; index++) {
    const latitude = (levels[index - 1] + levels[index]) / 2
    const xs = horizontalCrossings(ring, latitude).sort((a, b) => a - b)
    for (let i = 1; i < xs.length; i += 2) {
      const width = xs[i] - xs[i - 1]
      if (width > widest) {
        widest = width
        point = [(xs[i] + xs[i - 1]) / 2, latitude]
      }
    }
  }
  return point
}

export function samplingPoints(cells: ZoneCell[]) {
  const largest = new Map<Zone, { cell: ZoneCell; area: number }>()
  for (const cell of cells) {
    const area = cellArea(cell)
    if (area > (largest.get(cell.zone)?.area ?? 0)) largest.set(cell.zone, { cell, area })
  }
  return zones.flatMap((zone) => {
    const cell = largest.get(zone)?.cell
    const position = cell && interiorPoint(cell.geometry)
    return position ? [{ zone, position }] : []
  })
}

function cellKey(cell: ZoneCell) {
  // Ring start and winding can differ even when the clipped grid cell is identical.
  return cell.geometry.coordinates[0].slice(0, -1).map(([x, y]) => x.toFixed(7) + ',' + y.toFixed(7)).sort().join('|')
}

export function compareHistory(layers: SceneLayer[]) {
  if (layers.length < 3) return undefined
  const [base, ...older] = layers
  const lookups = older.map((layer) => new Map(layer.cells.map((cell) => [cellKey(cell), cell.zone])))
  let persistent = 0
  let changing = 0
  let typical = 0
  let unmatched = 0
  const stableCells: ZoneCell[] = []
  for (const cell of base.cells) {
    const key = cellKey(cell)
    const area = cellArea(cell)
    const labels = lookups.map((lookup) => lookup.get(key))
    if (labels.some((label) => label === undefined)) unmatched += area
    else if (!labels.every((label) => label === cell.zone)) changing += area
    else if (cell.zone === 'typical') typical += area
    else {
      persistent += area
      stableCells.push(cell)
    }
  }
  return { persistent, changing, typical, unmatched, stableCells }
}

export function downloadGeoJson(name: string, features: object[]) {
  const blob = new Blob([JSON.stringify({ type: 'FeatureCollection', features })], { type: 'application/geo+json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name + '.geojson'
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
