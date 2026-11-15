import type { Polygon, Position } from '../../api/contracts'

export const maxFieldVertices = 127

export function polygonFrom(points: Position[]): Polygon {
  if (points.length < 3) throw new Error('Provide at least three different vertices.')
  if (points.some(([lon, lat]) => !Number.isFinite(lon) || !Number.isFinite(lat)
    || lon < -180 || lon > 180 || lat < -90 || lat > 90)) {
    throw new Error('Each vertex must contain a valid longitude and latitude.')
  }
  const first = points[0]
  const last = points.at(-1)
  const open = first[0] === last?.[0] && first[1] === last?.[1] ? points.slice(0, -1) : points
  if (open.length > maxFieldVertices) throw new Error('A field can have at most 127 vertices.')
  if (new Set(open.map((item) => item.join(','))).size !== open.length || open.length < 3) {
    throw new Error('Each polygon vertex must be unique.')
  }
  const area = open.reduce((sum, [x, y], index) => {
    const [nextX, nextY] = open[(index + 1) % open.length]
    return sum + (x - first[0]) * (nextY - first[1]) - (nextX - first[0]) * (y - first[1])
  }, 0)
  if (Math.abs(area) < 1e-14) throw new Error('These vertices do not enclose an area.')
  return { type: 'Polygon', coordinates: [[...open, first]] }
}

export function estimateHectares(points: Position[]): number {
  if (points.length < 3) return 0
  const radians = Math.PI / 180
  const earthRadius = 6_371_008.8
  const originLatitude = Math.sin(points[0][1] * radians)
  const surface = points.reduce((sum, [longitude, latitude], index) => {
    const [nextLongitude, nextLatitude] = points[(index + 1) % points.length]
    const delta = ((nextLongitude - longitude + 540) % 360 - 180) * radians
    return sum + delta * (Math.sin(latitude * radians) + Math.sin(nextLatitude * radians) - 2 * originLatitude)
  }, 0)
  return Math.abs(surface) * earthRadius ** 2 / 20_000
}

export function parseVertices(text: string): Position[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const parts = line.split(',').map((part) => part.trim())
    if (parts.length !== 2 || parts.some((part) => part.length === 0)) {
      throw new Error('Enter one longitude,latitude pair per line.')
    }
    return [Number(parts[0]), Number(parts[1])]
  })
}

export function moistureColor(value: number, minimum: number, maximum: number): string {
  const ratio = maximum === minimum ? .5 : Math.min(1, Math.max(0, (value - minimum) / (maximum - minimum)))
  const stops = [[249, 218, 91], [74, 197, 185], [28, 136, 191], [29, 62, 145]]
  const position = ratio * (stops.length - 1)
  const index = Math.min(stops.length - 2, Math.floor(position))
  const rgb = stops[index].map((channel, i) => Math.round(channel + (stops[index + 1][i] - channel) * (position - index)))
  return 'rgb(' + rgb.join(',') + ')'
}
