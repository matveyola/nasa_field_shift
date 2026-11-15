import { useLayoutEffect } from 'react'
import * as L from 'leaflet'
import type { TerrainResult } from '../../api/analytics'
import type { Polygon } from '../../api/contracts'
import type { FieldMapVisualization } from './fieldMapVisualization'
import { fieldColorScale, renderFieldRaster, type RasterGeometry } from './fieldRasterRenderer'
import { moistureColor } from './geometry'
import { spectralCell, spectralColorStops } from './spectralIndices'
import { zoneColors, zones } from './precision/zones'

const rasterPane = 'analyticsPane'
const svgNamespace = 'http://www.w3.org/2000/svg'
let nextMaskId = 0

function addRaster(
  map: L.Map, opacity: number, options: Omit<Parameters<typeof renderFieldRaster>[0], 'project'>,
) {
  // Leaflet's projected world coordinates stay fixed through pan and zoom.
  const zoom = 19
  const project: Parameters<typeof renderFieldRaster>[0]['project'] = ([longitude, latitude]) => {
    const { x, y } = map.project([latitude, longitude], zoom)
    return [x, y]
  }
  const raster = renderFieldRaster({ ...options, project })
  if (!raster) return
  const [left, top, right, bottom] = raster.bounds
  const bounds = L.latLngBounds(map.unproject([left, top], zoom), map.unproject([right, bottom], zoom))
  const width = right - left
  const height = bottom - top
  const maskId = `field-raster-mask-${++nextMaskId}`
  const svg = document.createElementNS(svgNamespace, 'svg')
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`)
  svg.setAttribute('preserveAspectRatio', 'none')
  svg.setAttribute('aria-hidden', 'true')
  function addClip(id: string, geometries: readonly RasterGeometry[]) {
    const mask = document.createElementNS(svgNamespace, 'clipPath')
    mask.id = id
    const path = document.createElementNS(svgNamespace, 'path')
    const rings = geometries.flatMap((geometry) => {
      const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
      return polygons.flatMap((polygon) => polygon.map((ring, index) => {
        const points = ring.map(project).map(([x, y]) => [x - left, y - top])
        const area = points.reduce((sum, [x, y], i) => {
          const [nextX, nextY] = points[(i + 1) % points.length]
          return sum + x * nextY - nextX * y
        }, 0)
        if ((area > 0) !== (index === 0)) points.reverse()
        return 'M' + points.map(([x, y]) => `${x},${y}`).join('L') + 'Z'
      }))
    })
    // Align ring winding for the union of coverage cells and each polygon's holes.
    path.setAttribute('d', rings.join(' '))
    mask.append(path)
    svg.append(mask)
  }
  addClip(maskId, [options.geometry])
  const coverageId = maskId + '-coverage'
  addClip(coverageId, options.cells.filter(({ value }) => Number.isFinite(value)).map(({ geometry }) => geometry))
  const image = document.createElementNS(svgNamespace, 'image')
  image.setAttribute('href', raster.canvas.toDataURL())
  image.setAttribute('width', String(width))
  image.setAttribute('height', String(height))
  image.setAttribute('preserveAspectRatio', 'none')
  image.setAttribute('clip-path', `url(#${coverageId})`)
  if (options.categorical) image.style.imageRendering = 'pixelated'
  const clipped = document.createElementNS(svgNamespace, 'g')
  clipped.setAttribute('clip-path', `url(#${maskId})`)
  clipped.append(image)
  svg.append(clipped)
  const overlay = L.svgOverlay(svg, bounds, {
    pane: rasterPane, opacity, interactive: false, className: 'field-raster',
  }).addTo(map)
  return () => { overlay.remove() }
}

function addTerrain(map: L.Map, terrain: TerrainResult) {
  const renderer = L.canvas({ pane: 'terrainPane', padding: .2 })
  const group = L.layerGroup().addTo(map)
  const style = { renderer, pane: 'terrainPane', color: '#ffffff', weight: 2, opacity: .95, interactive: false }
  function draw() {
    group.clearLayers()
    for (const vector of terrain.vectors) {
      const start = L.latLng(vector.start[1], vector.start[0])
      const end = L.latLng(vector.end[1], vector.end[0])
      const from = map.latLngToLayerPoint(start)
      const to = map.latLngToLayerPoint(end)
      const delta = to.subtract(from)
      const length = Math.hypot(delta.x, delta.y)
      if (length < 5) continue
      L.polyline([start, end], { ...style, color: '#0d0d0d', weight: 4 }).addTo(group)
      L.polyline([start, end], style).addTo(group)
      const size = Math.min(7, length * .38)
      const back = delta.multiplyBy(-size / length)
      const left = to.add(L.point(back.x - back.y * .5, back.y + back.x * .5))
      const right = to.add(L.point(back.x + back.y * .5, back.y - back.x * .5))
      L.polyline([map.layerPointToLatLng(left), end, map.layerPointToLatLng(right)], style).addTo(group)
    }
  }
  draw()
  map.on('zoomend', draw)
  return () => { map.off('zoomend', draw); group.remove(); renderer.remove() }
}

export function useAnalyticsMapLayers(map: L.Map | undefined, field: Polygon | undefined, view?: FieldMapVisualization) {
  useLayoutEffect(() => {
    if (!map || !view) return
    if (view.kind === 'vegetation') {
      const { geometry, layer, index } = view
      if (index === 'relative') {
        return addRaster(map, .96, {
          geometry, categorical: true,
          cells: layer.cells.map((cell) => ({ geometry: cell.geometry, value: zones.indexOf(cell.zone) })),
          color: (value) => zoneColors[zones[value]],
        })
      }
      return addRaster(map, .96, {
        geometry,
        cells: layer.cells.flatMap((cell) => {
          const value = spectralCell(cell, index)
          return value == null ? [] : [{ geometry: cell.geometry, value }]
        }),
        color: fieldColorScale(view.colorStops ?? spectralColorStops(index)),
      })
    }
    if (view.kind === 'terrain' && view.result.status === 'ready') return addTerrain(map, view.result)
    if (view.kind !== 'moisture' || !field) return
    const { layer } = view
    if (layer.demo) {
      return addRaster(map, .94, {
        geometry: field, cells: layer.cells,
        color: (value) => moistureColor(value, layer.minimum, layer.maximum),
      })
    }
    // Operational moisture is a coarse estimate, so only outline its sampling footprint.
    const model = layer.source?.kind === 'ml'
    const outline = L.geoJSON(layer.cells.map(({ geometry }) => geometry), {
      pane: rasterPane,
      style: { color: model ? '#0d0d0d' : '#ffffff', fill: false, dashArray: '8 8',
        dashOffset: model ? '8' : '0', weight: 3, interactive: false },
    }).addTo(map)
    return () => { outline.remove() }
  }, [map, field, view])
}
