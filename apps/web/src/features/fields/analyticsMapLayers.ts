import { useEffect } from 'react'
import * as L from 'leaflet'
import type { SpectralIndex, TerrainResult, VegetationResult } from '../../api/analytics'
import { ndviColor } from './vegetationInsights'
import { spectralCell, spectralColor } from './spectralIndices'

export const zoneColors = { low: '#c19548', typical: '#a0b58d', high: '#2b5940' } as const

export function useAnalyticsMapLayers(map: L.Map | undefined, vegetation?: VegetationResult['heterogeneity'], terrain?: TerrainResult, index: 'relative' | SpectralIndex = 'relative') {
  useEffect(() => {
    if (!map || !vegetation) return
    const renderer = L.canvas({ pane: 'vegetationPane', padding: .2 })
    const group = L.layerGroup().addTo(map)
    for (const cell of vegetation.cells) {
      const value = index === 'relative' ? cell.value : spectralCell(cell, index)
      if (value == null) continue
      const color = index === 'relative' ? zoneColors[cell.zone] : index === 'ndvi' ? ndviColor(value) : spectralColor(value, index)
      L.geoJSON(cell.geometry, { style: {
        renderer, pane: 'vegetationPane', fillColor: color, fillOpacity: .74,
        stroke: false, interactive: false,
      } }).addTo(group)
    }
    return () => { group.remove(); renderer.remove() }
  }, [map, vegetation, index])

  useEffect(() => {
    if (!map || terrain?.status !== 'ready') return
    const renderer = L.canvas({ pane: 'terrainPane', padding: .2 })
    const group = L.layerGroup().addTo(map)
    const style = { renderer, pane: 'terrainPane', color: '#ffffff', weight: 2, opacity: .95, interactive: false }
    function draw() {
      group.clearLayers()
      for (const vector of terrain!.vectors) {
        const start = L.latLng(vector.start[1], vector.start[0])
        const end = L.latLng(vector.end[1], vector.end[0])
        const from = map!.latLngToLayerPoint(start)
        const to = map!.latLngToLayerPoint(end)
        const delta = to.subtract(from)
        const length = Math.hypot(delta.x, delta.y)
        if (length < 5) continue
        L.polyline([start, end], { ...style, color: '#0d0d0d', weight: 4 }).addTo(group)
        L.polyline([start, end], style).addTo(group)
        const size = Math.min(7, length * .38)
        const back = delta.multiplyBy(-size / length)
        const left = to.add(L.point(back.x - back.y * .5, back.y + back.x * .5))
        const right = to.add(L.point(back.x + back.y * .5, back.y - back.x * .5))
        L.polyline([map!.layerPointToLatLng(left), end, map!.layerPointToLatLng(right)], style).addTo(group)
      }
    }
    draw()
    map.on('zoomend', draw)
    return () => { map.off('zoomend', draw); group.remove(); renderer.remove() }
  }, [map, terrain])
}
