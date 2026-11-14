import type { Position } from '../../api/contracts'
import { polygonFrom } from './geometry.ts'

export async function readGeoJsonBoundary(file: File): Promise<{ points: Position[]; name?: string }> {
  if (file.size > 256 * 1024) throw new Error('Choose a GeoJSON file under 256 KB.')
  let raw: unknown
  try {
    raw = JSON.parse((await file.text()).replace(/^\uFEFF/, ''))
  } catch {
    throw new Error('The file is not valid GeoJSON.')
  }
  const result = parseBoundary(raw)
  if (!result) throw new Error('Choose GeoJSON with one WGS84 polygon and no holes.')
  return result
}

function parseBoundary(raw: unknown) {
  if (!isObject(raw) || raw.crs !== undefined) return
  let item = raw
  if (item.type === 'FeatureCollection') {
    if (!Array.isArray(item.features) || item.features.length !== 1 || !isObject(item.features[0])) return
    item = item.features[0]
  }
  if (item.crs !== undefined) return
  const properties = isObject(item.properties) ? item.properties : undefined
  const geometry = item.type === 'Feature' ? item.geometry : item
  if (!isObject(geometry) || geometry.crs !== undefined || geometry.type !== 'Polygon'
    || !Array.isArray(geometry.coordinates) || geometry.coordinates.length !== 1) return
  const ring: unknown = geometry.coordinates[0]
  if (!Array.isArray(ring) || ring.length > 128) return
  const points: Position[] = []
  for (const vertex of ring) {
    if (!Array.isArray(vertex) || (vertex.length !== 2 && vertex.length !== 3)
      || !vertex.every((coordinate: unknown) => typeof coordinate === 'number' && Number.isFinite(coordinate))) return
    points.push([vertex[0], vertex[1]])
  }
  return {
    points: polygonFrom(points).coordinates[0].slice(0, -1),
    name: typeof properties?.name === 'string' ? properties.name.trim().slice(0, 80) : undefined,
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
