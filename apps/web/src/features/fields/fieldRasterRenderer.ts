import type { Polygon, Position } from '../../api/contracts'

type Point = readonly [number, number]
type RGB = readonly [number, number, number]
type ColorStop = { value: number; color: string }
type Bounds = [left: number, top: number, right: number, bottom: number]
type ProjectedPolygon = { rings: Point[][]; bounds: Bounds }
export type RasterGeometry = Polygon | { type: 'MultiPolygon'; coordinates: Position[][][] }
export type RasterColor = RGB | string

type RasterOptions = {
  geometry: RasterGeometry
  cells: readonly { geometry: RasterGeometry; value: number }[]
  project: (position: Position) => Point
  color: (value: number) => RasterColor
  categorical?: boolean
}

const maxSide = 768
const subsamples = 2

function rgb(color: RasterColor): RGB {
  if (typeof color !== 'string') return color
  const channels = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(color)
  if (channels) return [Number(channels[1]), Number(channels[2]), Number(channels[3])]
  const value = Number.parseInt(color.slice(1), 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

export function fieldColorScale(stops: readonly ColorStop[]) {
  const palette = stops.map(({ value, color }) => ({ value, color: rgb(color) }))
  return (value: number): RGB => {
    const first = palette[0]
    if (value <= first.value) return first.color
    for (let index = 1; index < palette.length; index += 1) {
      const start = palette[index - 1]
      const end = palette[index]
      if (value > end.value) continue
      const weight = (value - start.value) / (end.value - start.value)
      return [
        start.color[0] + weight * (end.color[0] - start.color[0]),
        start.color[1] + weight * (end.color[1] - start.color[1]),
        start.color[2] + weight * (end.color[2] - start.color[2]),
      ]
    }
    return palette[palette.length - 1].color
  }
}

function projectPolygons(geometry: RasterGeometry, project: RasterOptions['project']) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  return polygons.flatMap((polygon): ProjectedPolygon[] => {
    const rings = polygon.map((ring) => ring.map(project))
    const bounds = polygonBounds(rings[0] ?? [])
    return bounds[0] < bounds[2] && bounds[1] < bounds[3] ? [{ rings, bounds }] : []
  })
}

function polygonBounds(ring: Point[]): Bounds {
  const bounds: Bounds = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [x, y] of ring) extendBounds(bounds, [x, y, x, y])
  return bounds
}

function extendBounds(bounds: Bounds, other: Bounds) {
  bounds[0] = Math.min(bounds[0], other[0])
  bounds[1] = Math.min(bounds[1], other[1])
  bounds[2] = Math.max(bounds[2], other[2])
  bounds[3] = Math.max(bounds[3], other[3])
  return bounds
}

function unionBounds(polygons: ProjectedPolygon[]) {
  return polygons.reduce((bounds, polygon) => extendBounds(bounds, polygon.bounds), [Infinity, Infinity, -Infinity, -Infinity] as Bounds)
}

function typicalSpan(polygons: ProjectedPolygon[], axis: 0 | 1) {
  const spans = polygons.map(({ bounds }) => bounds[axis + 2] - bounds[axis]).sort((a, b) => a - b)
  return spans[Math.floor(spans.length / 2)]
}

function rasterize(
  polygons: ProjectedPolygon[], target: Uint8Array | Uint32Array, value: number,
  bounds: Bounds, width: number, height: number,
) {
  const [left, top, right, bottom] = bounds
  const scaleX = width / (right - left)
  const scaleY = height / (bottom - top)
  const intersections: number[] = []
  for (const polygon of polygons) {
    const startRow = Math.max(0, Math.ceil((polygon.bounds[1] - top) * scaleY - .5))
    const endRow = Math.min(height, Math.ceil((polygon.bounds[3] - top) * scaleY - .5))
    for (let row = startRow; row < endRow; row += 1) {
      const y = top + (row + .5) / scaleY
      intersections.length = 0
      // Even-odd coverage preserves holes independent of GeoJSON ring winding.
      for (const ring of polygon.rings) {
        for (let current = 0, previous = ring.length - 1; current < ring.length; previous = current++) {
          const [x0, y0] = ring[previous]
          const [x1, y1] = ring[current]
          if ((y0 > y) !== (y1 > y)) intersections.push(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        }
      }
      intersections.sort((a, b) => a - b)
      for (let index = 0; index + 1 < intersections.length; index += 2) {
        const start = Math.max(0, Math.ceil((intersections[index] - left) * scaleX - .5))
        const end = Math.min(width, Math.ceil((intersections[index + 1] - left) * scaleX - .5))
        if (start < end) target.fill(value, row * width + start, row * width + end)
      }
    }
  }
}

function smoothRuns(
  source: Float32Array, coverage: Uint32Array, width: number, height: number, radius: number, horizontal: boolean,
) {
  if (radius < 1) return source
  const output = new Float32Array(source.length)
  const lineCount = horizontal ? height : width
  const length = horizontal ? width : height
  const stride = horizontal ? 1 : width
  const lineStride = horizontal ? width : 1
  for (let line = 0; line < lineCount; line += 1) {
    const offset = line * lineStride
    let start = 0
    while (start < length) {
      if (!coverage[offset + start * stride]) {
        start += 1
        continue
      }
      let end = start + 1
      while (end < length && coverage[offset + end * stride]) end += 1
      let from = start
      let to = start
      let sum = 0
      // Each averaging pass stops at the first missing sample in its direction.
      for (let sample = start; sample < end; sample += 1) {
        while (to < end && to <= sample + radius) sum += source[offset + to++ * stride]
        while (from < sample - radius) sum -= source[offset + from++ * stride]
        output[offset + sample * stride] = sum / (to - from)
      }
      start = end
    }
  }
  return output
}

export function buildFieldRaster({ geometry, cells, project, color, categorical = false }: RasterOptions) {
  const field = projectPolygons(geometry, project)
  const available = cells.filter(({ value }) => Number.isFinite(value)).map(({ geometry, value }) => ({
    value, polygons: projectPolygons(geometry, project),
  }))
  const footprints = available.flatMap(({ polygons }) => polygons)
  if (!field.length || !footprints.length) return undefined

  const fieldBounds = unionBounds(field)
  const coverageBounds = unionBounds(footprints)
  const bounds: Bounds = [Math.max(fieldBounds[0], coverageBounds[0]), Math.max(fieldBounds[1], coverageBounds[1]),
    Math.min(fieldBounds[2], coverageBounds[2]), Math.min(fieldBounds[3], coverageBounds[3])]
  const spanX = bounds[2] - bounds[0]
  const spanY = bounds[3] - bounds[1]
  if (!(spanX > 0 && spanY > 0)) return undefined

  const cellWidth = typicalSpan(footprints, 0)
  const cellHeight = typicalSpan(footprints, 1)
  const longest = Math.max(spanX, spanY)
  const scale = Math.min(maxSide / longest, Math.max(192 / longest, 8 / Math.min(cellWidth, cellHeight)))
  const width = Math.ceil(spanX * scale)
  const height = Math.ceil(spanY * scale)
  const sampleWidth = width * subsamples
  const sampleHeight = height * subsamples
  const sampleCount = sampleWidth * sampleHeight

  const fieldMask = new Uint8Array(sampleCount)
  rasterize(field, fieldMask, 1, bounds, sampleWidth, sampleHeight)
  const coverage = new Uint32Array(sampleCount)
  available.forEach(({ polygons }, index) => rasterize(polygons, coverage, index + 1, bounds, sampleWidth, sampleHeight))
  let hasCoverage = false
  for (let sample = 0; sample < sampleCount; sample += 1) {
    if (!fieldMask[sample]) coverage[sample] = 0
    if (coverage[sample]) hasCoverage = true
  }
  if (!hasCoverage) return undefined

  let values: Float32Array | undefined
  if (!categorical) {
    values = new Float32Array(sampleCount)
    for (let sample = 0; sample < sampleCount; sample += 1) {
      if (coverage[sample]) values[sample] = available[coverage[sample] - 1].value
    }
    // Three small box passes approximate a Gaussian transition within one source cell.
    // Every pass remains coverage-aware, including along holes and missing-data boundaries.
    const radiusX = Math.floor(cellWidth * sampleWidth / spanX / 6)
    const radiusY = Math.floor(cellHeight * sampleHeight / spanY / 6)
    for (let pass = 0; pass < 3; pass += 1) {
      values = smoothRuns(values, coverage, sampleWidth, sampleHeight, radiusX, true)
      values = smoothRuns(values, coverage, sampleWidth, sampleHeight, radiusY, false)
    }
  }

  const data = new Uint8ClampedArray(width * height * 4)
  const cellColors = categorical ? available.map(({ value }) => rgb(color(value))) : undefined
  for (let row = 0; row < height; row += 1) {
    for (let column = 0; column < width; column += 1) {
      let count = 0
      let sum = 0
      let firstCell = 0
      for (let dy = 0; dy < subsamples; dy += 1) {
        for (let dx = 0; dx < subsamples; dx += 1) {
          const sample = (row * subsamples + dy) * sampleWidth + column * subsamples + dx
          if (!coverage[sample]) continue
          firstCell ||= coverage[sample]
          count += 1
          sum += values?.[sample] ?? 0
        }
      }
      if (!count) continue
      const [red, green, blue] = cellColors ? cellColors[firstCell - 1] : rgb(color(sum / count))
      const offset = (row * width + column) * 4
      data[offset] = red
      data[offset + 1] = green
      data[offset + 2] = blue
      data[offset + 3] = 255 * count / (subsamples * subsamples)
    }
  }
  return { bounds, width, height, data }
}

export function renderFieldRaster(options: RasterOptions) {
  const raster = buildFieldRaster(options)
  if (!raster) return undefined
  const { bounds, width, height, data } = raster
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) return undefined
  context.putImageData(new ImageData(data, width, height), 0, 0)
  return { canvas, bounds }
}
