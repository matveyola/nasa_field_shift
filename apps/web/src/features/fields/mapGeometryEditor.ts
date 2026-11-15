import { useEffect, useEffectEvent, useRef } from 'react'
import * as L from 'leaflet'
import type { Position } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { coordinateFromMap } from './basemaps'
import { maxFieldVertices } from './geometry'
import './mapGeometryEditor.css'

export type EditMode = 'boundary' | 'planting'

type Options = {
  editMode?: EditMode
  editLocked?: boolean
  activeVertex?: number
  selectVertex?: (index: number) => void
  moveVertex?: (index: number, point: Position) => void
  insertVertex?: (index: number, point: Position) => void
}

const arrows = new Map([
  ['ArrowLeft', [-1, 0]], ['ArrowRight', [1, 0]], ['ArrowUp', [0, -1]], ['ArrowDown', [0, 1]],
])

function makeHandle(position: L.LatLng, kind: 'vertex' | 'midpoint', label: string, size: number) {
  const marker = L.marker(position, {
    icon: L.divIcon({
      className: `field-editor-handle field-editor-${kind}`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    }),
    title: label,
    draggable: kind === 'vertex',
    autoPan: kind === 'vertex',
    bubblingMouseEvents: false,
    zIndexOffset: kind === 'vertex' ? 1000 : 500,
  })
  marker.on('add', () => marker.getElement()?.setAttribute('aria-label', label))
  return marker
}

export function useMapGeometryEditor(map: L.Map | undefined, points: Position[], options: Options) {
  const { t } = useLocale()
  const { editMode, editLocked, activeVertex, selectVertex, moveVertex, insertVertex } = options
  const handles = useRef<L.Marker[]>([])
  const focusVertex = useRef<number | undefined>(undefined)
  const select = useEffectEvent((index: number) => { if (!editLocked) selectVertex?.(index) })
  const move = useEffectEvent((index: number, point: Position) => { if (!editLocked) moveVertex?.(index, point) })
  const insert = useEffectEvent((index: number, point: Position) => { if (!editLocked) insertVertex?.(index, point) })

  useEffect(() => {
    if (!map || !editMode || !points.length) return

    const group = L.layerGroup().addTo(map)
    const positions = points.map(([longitude, latitude]) => L.latLng(latitude, longitude))
    const coarsePointer = matchMedia('(pointer: coarse)').matches
    const vertexSize = coarsePointer ? 36 : 30
    const midpointSize = coarsePointer ? 32 : 26
    const outlineStyle: L.PolylineOptions = {
      pane: 'geometryDraftPane',
      color: '#ffffff',
      fillColor: '#ffffff',
      fillOpacity: .15,
      weight: 3,
      interactive: false,
    }
    const haloStyle = { ...outlineStyle, color: '#101710', weight: 7, opacity: .7, fill: false }
    const halo = positions.length >= 3 ? L.polygon(positions, haloStyle) : L.polyline(positions, haloStyle)
    halo.addTo(group)
    const outline = positions.length >= 3 ? L.polygon(positions, outlineStyle) : L.polyline(positions, outlineStyle)
    outline.addTo(group)
    if (editLocked) {
      focusVertex.current = undefined
      return () => { group.remove() }
    }

    const midpointPosition = (index: number) => {
      const a = positions[index]
      const b = positions[(index + 1) % positions.length]
      return L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2)
    }

    // Two vertices have one edge. A polygon also has an edge from the last vertex to the first.
    const edgeCount = positions.length >= maxFieldVertices ? 0 : positions.length >= 3 ? positions.length : positions.length - 1
    const midpoints = Array.from({ length: edgeCount }, (_, index) => {
      const marker = makeHandle(midpointPosition(index), 'midpoint', `${t('Insert vertex after')} ${index + 1}`, midpointSize)
        .addTo(group)
      function add(keyboard: boolean) {
        if (keyboard) focusVertex.current = index + 1
        insert(index + 1, coordinateFromMap(midpointPosition(index).wrap()))
      }
      marker.on('click', () => add(false))
      marker.getElement()?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        event.stopPropagation()
        add(true)
      })
      return marker
    })

    function updateMidpoint(index: number) {
      const marker = midpoints[index]
      if (!map || !marker) return
      const a = map.latLngToLayerPoint(positions[index])
      const b = map.latLngToLayerPoint(positions[(index + 1) % positions.length])
      marker.setLatLng(midpointPosition(index))
      // Hide insert controls when they would overlap the adjacent drag handles.
      marker.getElement()?.toggleAttribute('hidden', a.distanceTo(b) < vertexSize + midpointSize)
    }

    function updateMidpoints() {
      midpoints.forEach((_, index) => updateMidpoint(index))
    }

    handles.current = positions.map((position, index) => {
      const marker = makeHandle(position, 'vertex', `${t('Vertex')} ${index + 1}`, vertexSize).addTo(group)
      marker.on('click dragstart', () => select(index))
      marker.on('drag', () => {
        positions[index] = marker.getLatLng()
        halo.setLatLngs(positions)
        outline.setLatLngs(positions)
        updateMidpoint(index)
        updateMidpoint((index + positions.length - 1) % positions.length)
      })
      marker.on('dragend', () => move(index, coordinateFromMap(marker.getLatLng().wrap())))
      marker.getElement()?.addEventListener('keydown', (event) => {
        const direction = arrows.get(event.key)
        if (event.key !== 'Enter' && event.key !== ' ' && !direction) return
        event.preventDefault()
        event.stopPropagation()
        select(index)
        if (!direction) return

        const step = event.shiftKey ? 18 : 4
        const origin = map.latLngToContainerPoint(marker.getLatLng())
        const target = map.containerPointToLatLng(origin.add(L.point(direction[0] * step, direction[1] * step)))
        focusVertex.current = index
        move(index, coordinateFromMap(target.wrap()))
      })
      return marker
    })

    if (focusVertex.current !== undefined) {
      handles.current[focusVertex.current]?.getElement()?.focus({ preventScroll: true })
      focusVertex.current = undefined
    }
    updateMidpoints()
    map.on('zoomend', updateMidpoints)
    return () => {
      map.off('zoomend', updateMidpoints)
      group.remove()
      handles.current = []
    }
  }, [map, points, editMode, editLocked, t])

  useEffect(() => {
    handles.current.forEach((marker, index) => {
      marker.getElement()?.setAttribute('aria-pressed', String(index === activeVertex))
    })
  }, [map, points, editMode, editLocked, activeVertex, t])
}
