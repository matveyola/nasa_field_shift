import type { Position } from '../../../api/contracts'
import { inRing, zones, type Zone, type ZoneCell } from './zones.ts'

export type MeasurementKind = 'yield' | 'soil'
type Group = 'control' | 'variable' | 'unlabelled'
type Measurement = { position: Position; value: number; group: Group }

export async function readMeasurements(file: File, kind: MeasurementKind): Promise<Measurement[]> {
  if (file.size > 512 * 1024) throw new Error('Choose a CSV file under 512 KB.')
  const [header, ...rows] = (await file.text()).replace(/^\uFEFF/, '').trim().split(/\r?\n/).filter((line) => line.trim())
  const required = 'longitude,latitude,' + (kind === 'yield' ? 'yield_t_ha' : 'ph')
  const grouped = kind === 'yield' && header?.trim() === required + ',group'
  if (!grouped && header?.trim() !== required) throw new Error('The CSV header does not match the selected measurement.')
  if (rows.length === 0 || rows.length > 4000) throw new Error('Import between 1 and 4,000 measurement rows.')

  return rows.map((row) => {
    const columns = row.split(',').map((value) => value.trim())
    const [longitude, latitude, value] = columns.slice(0, 3).map(Number)
    const group = grouped ? columns[3] : 'unlabelled'
    if (columns.length !== (grouped ? 4 : 3) || columns.slice(0, 3).some((value) => !value)
      || !Number.isFinite(longitude) || Math.abs(longitude) > 180
      || !Number.isFinite(latitude) || Math.abs(latitude) > 90
      || !Number.isFinite(value) || value < 0 || value > (kind === 'yield' ? 300 : 14)
      || (group !== 'control' && group !== 'variable' && group !== 'unlabelled')) {
      throw new Error('The CSV contains invalid coordinates, values or groups.')
    }
    return { position: [longitude, latitude], value, group }
  })
}

export function summarizeMeasurements(points: Measurement[], cells: ZoneCell[]) {
  const index = cells.map((cell) => {
    const ring = cell.geometry.coordinates[0]
    const longitudes = ring.map(([x]) => x)
    const latitudes = ring.map(([, y]) => y)
    return { ring, zone: cell.zone, west: Math.min(...longitudes), east: Math.max(...longitudes),
      south: Math.min(...latitudes), north: Math.max(...latitudes) }
  })
  const summaries = new Map<Zone, { group: Group; value: number }[]>(zones.map((zone) => [zone, []]))
  let excluded = 0
  for (const { position, value, group } of points) {
    const [longitude, latitude] = position
    let zone: Zone | undefined
    let ambiguous = false
    for (const cell of index) {
      if (longitude < cell.west || longitude > cell.east || latitude < cell.south || latitude > cell.north
        || !inRing(position, cell.ring)) continue
      if (zone && zone !== cell.zone) { ambiguous = true; break }
      zone = cell.zone
    }
    if (zone && !ambiguous) summaries.get(zone)!.push({ value, group })
    else excluded++
  }
  return {
    excluded,
    rows: zones.map((zone) => {
      const samples = summaries.get(zone)!
      const mean = (items: typeof samples) => items.length ? items.reduce((total, item) => total + item.value, 0) / items.length : null
      const control = samples.filter((item) => item.group === 'control')
      const variable = samples.filter((item) => item.group === 'variable')
      return { zone, count: samples.length, value: mean(samples), control: mean(control), variable: mean(variable),
        controlCount: control.length, variableCount: variable.length }
    }),
  }
}
