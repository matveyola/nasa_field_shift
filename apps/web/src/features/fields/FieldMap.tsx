import { useLocale } from '../../shared/locale'
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from 'react'
import { MinusIcon, PlusIcon, ViewfinderCircleIcon } from '@heroicons/react/24/outline'
import * as L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Field, Position } from '../../api/contracts'
import { basemaps, coordinateFromMap, demoAerialBounds, usgsAttribution, type BasemapId } from './basemaps'
import { useMapGeometryEditor, type EditMode } from './mapGeometryEditor'
import type { SatelliteRaster } from './satelliteRaster'
import { useAnalyticsMapLayers } from './analyticsMapLayers'
import { useDetailedImagery, usePlaceLabels } from './mapImagery'
import type { FieldMapVisualization } from './fieldMapVisualization'
import type { PreparedContour } from '../../api/boundaries'
import type { MapWindow } from './PreparedContoursPanel'

type Props = {
  fields: Field[]
  selected?: Field
  visualization?: FieldMapVisualization
  showBoundaries: boolean
  bottomOcclusion?: number
  satellite?: SatelliteRaster
  points: Position[]
  drawing: boolean
  editMode?: EditMode
  editLocked?: boolean
  appendVertices?: boolean
  activeVertex?: number
  selectVertex?: (index: number) => void
  moveVertex?: (index: number, point: Position) => void
  insertVertex?: (index: number, point: Position) => void
  newBoundary?: boolean
  suggestionSeed?: Position
  locationMarker?: Position
  suggestBoundary?: (position: Position, fieldId?: string) => void
  reportSuggestionCenter?: (position: Position) => void
  basemap: BasemapId
  retryTiles: number
  chooseField: (id: string) => void
  addPoint: (point: Position) => void
  onBasemapFailure: (failed: BasemapId, hasLocalAerial: boolean) => void
  onBasemapReady: () => void
  preparedContours?: PreparedContour[]
  adoptContour?: (contour: PreparedContour) => void
  contourFocus?: Position
  onViewport?: (window: MapWindow) => void
  /** Explicit navigation for setup; ordinary field views keep their existing automatic fit. */
  viewportRequest?: { key: string; bounds: [[number, number], [number, number]]; maxZoom?: number }
    | { key: string; center: Position; zoom: number }
  autoFitFields?: boolean
}

function fitFields(map: L.Map, fields: Field[], editPoints?: Position[]) {
  const bounds = L.latLngBounds([])
  for (const field of fields) bounds.extend(L.geoJSON(field.geometry).getBounds())
  for (const [longitude, latitude] of editPoints ?? []) bounds.extend([latitude, longitude])
  if (!bounds.isValid()) return
  map.fitBounds(bounds, {
    animate: false, maxZoom: editPoints ? 18 : 17,
    paddingTopLeft: editPoints ? [22, 60] : [22, 85], paddingBottomRight: editPoints ? [60, 55] : [22, 122],
  })
}

export default function FieldMap({
  fields, selected, visualization, showBoundaries, satellite, points, drawing, basemap, retryTiles, chooseField, addPoint,
  onBasemapFailure, onBasemapReady, editMode, editLocked, appendVertices, activeVertex, selectVertex, moveVertex, insertVertex,
  newBoundary, suggestionSeed, locationMarker, suggestBoundary, reportSuggestionCenter,
  bottomOcclusion = 0,
  preparedContours, adoptContour, contourFocus, onViewport,
  viewportRequest, autoFitFields = true,
}: Props) {
  const { t } = useLocale()
  const host = useRef<HTMLDivElement>(null)
  const detailedImageryVisible = useRef(false)
  const [map, setMap] = useState<L.Map>()
  const viewBeforeSheet = useRef<{ fieldId: string; center: L.LatLng; zoom: number } | undefined>(undefined)
  const selectedId = selected?.id
  const planting = selected?.planting
  const editing = drawing || Boolean(editMode)
  const suggesting = Boolean(suggestBoundary)
  const activeVisualization = editing || suggesting || satellite ? undefined : visualization
  const satelliteView = satellite ?? (activeVisualization?.kind === 'satellite' ? activeVisualization.raster : undefined)
  const showPlanting = activeVisualization?.kind === 'planting'
  const showOutlineFill = !activeVisualization
  const adding = editMode ? !editLocked && Boolean(appendVertices) : drawing
  const handleMapClick = useEffectEvent(({ latlng }: L.LeafletMouseEvent) => {
    const position = coordinateFromMap(latlng.wrap())
    if (suggestBoundary) suggestBoundary(position)
    else if (adding) addPoint(position)
  })
  const select = useEffectEvent((fieldId: string, event: L.LeafletMouseEvent) => {
    if (suggestBoundary) suggestBoundary(coordinateFromMap(event.latlng.wrap()), fieldId)
    else if (!editing) chooseField(fieldId)
  })
  const fitDraft = useEffectEvent((instance: L.Map) =>
    fitFields(instance, selected && !newBoundary ? [selected] : [], points))
  const reportCenter = useEffectEvent((instance: L.Map) =>
    reportSuggestionCenter?.(coordinateFromMap(instance.getCenter().wrap())))
  const signalFailure = useEffectEvent((failed: BasemapId, hasLocalAerial: boolean) => onBasemapFailure(failed, hasLocalAerial))
  const signalReady = useEffectEvent(() => onBasemapReady())
  const reportViewport = useEffectEvent(() => {
    if (!map || !onViewport) return
    const bounds = map.getBounds()
    onViewport({ west: bounds.getWest(), east: bounds.getEast(), south: bounds.getSouth(), north: bounds.getNorth() })
  })
  const adopt = useEffectEvent((contour: PreparedContour) => adoptContour?.(contour))

  useEffect(() => {
    if (!map) return
    const report = () => reportViewport()
    report(); map.on('moveend', report)
    return () => { map.off('moveend', report) }
  }, [map])

  useEffect(() => {
    if (map && contourFocus) map.setView([contourFocus[1], contourFocus[0]], 14)
  }, [map, contourFocus])

  useEffect(() => {
    if (!map || editing || suggesting || !preparedContours?.length) return
    const group = L.layerGroup().addTo(map)
    for (const contour of preparedContours) {
      L.geoJSON(contour.geometry, { style: { color: '#ffffff', weight: 1.5, dashArray: '4 4', fillColor: '#ffffff', fillOpacity: .15 } })
        .on('click', (event: L.LeafletMouseEvent) => { L.DomEvent.stopPropagation(event); adopt(contour) }).addTo(group)
    }
    return () => { group.remove() }
  }, [map, preparedContours, editing, suggesting])

  useEffect(() => {
    const element = host.current
    if (!element) return
    const instance = L.map(element, { zoomControl: false, zoomAnimation: false, minZoom: 3, maxZoom: 19 })
      .setView([49, 31], 5)
    instance.attributionControl?.setPrefix(false)
    instance.createPane('satellitePane').style.zIndex = '300'
    const analyticsPane = instance.createPane('analyticsPane')
    analyticsPane.style.zIndex = '395'
    analyticsPane.style.pointerEvents = 'none'
    instance.createPane('terrainPane').style.zIndex = '415'
    instance.createPane('plantingPane').style.zIndex = '410'
    instance.createPane('geometryDraftPane').style.zIndex = '420'
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(instance)
    instance.on('click', handleMapClick)
    const observer = new ResizeObserver(() => instance.invalidateSize({ pan: false, debounceMoveend: true }))
    observer.observe(element)
    setMap(instance)
    return () => {
      observer.disconnect()
      instance.remove()
    }
  }, [])

  useEffect(() => {
    if (map && autoFitFields) fitFields(map, fields)
  }, [map, fields, autoFitFields])

  useEffect(() => {
    if (!map || !viewportRequest) return
    if ('center' in viewportRequest) {
      const [longitude, latitude] = viewportRequest.center
      map.setView([latitude, longitude], viewportRequest.zoom, { animate: false })
    } else {
      map.fitBounds(viewportRequest.bounds, {
        animate: false, padding: [24, 24], maxZoom: viewportRequest.maxZoom ?? 12,
      })
    }
  }, [map, viewportRequest])

  useEffect(() => {
    if (!map) return
    const previous = viewBeforeSheet.current
    if (previous && (bottomOcclusion === 0 || previous.fieldId !== selectedId || editing || suggesting)) {
      map.setView(previous.center, previous.zoom, { animate: false })
      viewBeforeSheet.current = undefined
    }
    if (!selected || bottomOcclusion === 0 || editing || suggesting) return
    const bounds = L.geoJSON(selected.geometry).getBounds()
    if (!bounds.isValid()) return
    viewBeforeSheet.current ??= { fieldId: selected.id, center: map.getCenter(), zoom: map.getZoom() }
    map.fitBounds(bounds, {
      animate: false,
      maxZoom: viewBeforeSheet.current.zoom,
      paddingTopLeft: [18, 64],
      paddingBottomRight: [18, bottomOcclusion + 12],
    })
  }, [map, selected, selectedId, bottomOcclusion, editing, suggesting])

  useEffect(() => {
    if (!map || !selected || editing || suggesting) return
    if (!map.getBounds().contains(L.geoJSON(selected.geometry).getBounds())) fitFields(map, [selected])
  }, [map, selected, editing, suggesting])

  useEffect(() => {
    if (map && editMode) fitDraft(map)
  }, [map, selected, editMode, newBoundary])

  useEffect(() => {
    if (!map || !suggesting) return
    const updateCenter = () => reportCenter(map)
    updateCenter()
    map.on('moveend', updateCenter)
    return () => { map.off('moveend', updateCenter) }
  }, [map, suggesting])

  useEffect(() => {
    if (!map) return
    const provider = basemaps[basemap]
    const localBounds = L.latLngBounds(demoAerialBounds)
    let localAerial: L.ImageOverlay | undefined
    let pendingLocalUpdate: ReturnType<typeof setTimeout> | undefined
    function hasLocalAerial() {
      return basemap === 'aerial' && map!.getZoom() >= 12 && localBounds.contains(map!.getCenter())
    }
    function syncLocalAerial() {
      if (!map) return
      if (hasLocalAerial() && !localAerial) {
        localAerial = L.imageOverlay('/bondurant-usgs-aerial.webp', localBounds, {
          pane: 'tilePane', zIndex: 3, interactive: false, alt: 'USGS aerial image of the Bondurant demo area',
          attribution: usgsAttribution,
        }).addTo(map)
      } else if (!hasLocalAerial() && localAerial) {
        localAerial.remove()
        localAerial = undefined
      }
    }
    syncLocalAerial()
    function queueLocalAerial() {
      clearTimeout(pendingLocalUpdate)
      pendingLocalUpdate = setTimeout(syncLocalAerial, 0)
    }
    map.on('moveend', queueLocalAerial)
    const tiles = L.tileLayer(provider.url, {
      attribution: provider.attribution, maxNativeZoom: provider.maxNativeZoom, maxZoom: 19, zIndex: 0,
      updateWhenIdle: true, keepBuffer: 1,
    })
    let successful = 0
    let errors = 0
    let timeout: ReturnType<typeof setTimeout> | undefined

    function tileLoaded() {
      successful += 1
      clearTimeout(timeout)
      signalReady()
    }
    function loadComplete() {
      clearTimeout(timeout)
      if (errors > 0 && successful === 0 && !detailedImageryVisible.current) {
        signalFailure(basemap, hasLocalAerial())
      }
    }
    function startLoading() {
      successful = 0
      errors = 0
      clearTimeout(timeout)
      timeout = setTimeout(() => {
        if (successful === 0 && !detailedImageryVisible.current) signalFailure(basemap, hasLocalAerial())
      }, 8000)
    }
    tiles.on('loading', startLoading).on('tileload', tileLoaded)
      .on('tileerror', () => { errors += 1 }).on('load', loadComplete).addTo(map)
    return () => {
      clearTimeout(timeout)
      clearTimeout(pendingLocalUpdate)
      tiles.remove().off()
      map.off('moveend', queueLocalAerial)
      localAerial?.remove()
    }
  }, [map, basemap, retryTiles])

  useDetailedImagery(map, basemap === 'aerial' && !satelliteView, retryTiles, detailedImageryVisible, onBasemapReady)
  usePlaceLabels(map, basemap === 'aerial')

  useLayoutEffect(() => {
    if (!map || !satelliteView) return
    const aerial = satelliteView.source === 'aerial'
    const overlay = L.imageOverlay(satelliteView.url, satelliteView.bounds, {
      pane: 'satellitePane', interactive: false,
      alt: aerial ? 'USGS aerial imagery used for the field outline' : 'Sentinel-2 true-color field imagery',
      attribution: aerial ? usgsAttribution
        : '<a href="https://registry.opendata.aws/sentinel-2-l2a-cogs/">Copernicus Sentinel-2</a>',
    }).addTo(map)
    if (suggesting && map.getZoom() < 14) map.fitBounds(satelliteView.bounds, { maxZoom: 15, animate: false })
    return () => { overlay.remove() }
  }, [map, satelliteView, suggesting])

  useEffect(() => {
    if (!map || (!showBoundaries && !editing && !suggesting)) return
    const group = L.layerGroup().addTo(map)
    for (const field of fields) {
      const active = field.id === selectedId
      const priorBoundary = active && editMode === 'boundary'
      // white outlines read on dark imagery; the chosen field gets a thicker line over a black halo
      if (active && !priorBoundary) {
        L.geoJSON(field.geometry, { style: { color: '#0d0d0d', weight: 5, opacity: .65, fill: false, interactive: false } }).addTo(group)
      }
      const outline = L.geoJSON(field.geometry, {
        style: {
          color: '#ffffff', weight: active && !priorBoundary ? 2.5 : 1.5,
          dashArray: priorBoundary ? '6 5' : undefined,
          fillColor: active ? '#ffffff' : '#0d0d0d',
          fillOpacity: editMode || suggesting || !showOutlineFill ? 0 : active ? .18 : .13,
          interactive: !editing, bubblingMouseEvents: false,
        },
      }).addTo(group)
      if (!editing) outline.on('click', (event: L.LeafletMouseEvent) => select(field.id, event))
      const polygon = outline.getLayers()[0]
      if (!editMode && !suggesting && (bottomOcclusion === 0 || active) && polygon instanceof L.Polygon) {
        const label = document.createElement('span')
        label.className = 'map-place-text'
        label.textContent = field.name
        L.marker(polygon.getCenter(), {
          icon: L.divIcon({
            html: label,
            className: 'map-place-label' + (active ? ' map-place-label-selected' : ''),
            iconSize: [0, 0],
            iconAnchor: [0, 0],
          }),
          keyboard: false,
          interactive: false,
        }).addTo(group)
      }
    }
    return () => { group.remove() }
  }, [map, fields, selectedId, editing, editMode, showBoundaries, suggesting, bottomOcclusion, showOutlineFill])

  useEffect(() => {
    const point = suggestionSeed ?? locationMarker
    if (!map || !point) return
    // Match the visible world copy when navigating on either side of the antimeridian.
    const [longitude, latitude] = point
    const nearestLongitude = longitude + 360 * Math.round((map.getCenter().lng - longitude) / 360)
    const marker = L.circleMarker([latitude, nearestLongitude], {
      pane: 'geometryDraftPane', radius: 7, weight: 2, color: '#ffffff',
      fillColor: suggestionSeed ? '#0d0d0d' : '#347757', fillOpacity: 1, interactive: false,
    }).addTo(map)
    return () => { marker.remove() }
  }, [map, suggestionSeed, locationMarker, viewportRequest])

  useLayoutEffect(() => {
    if (!map || !planting || (!showPlanting && editMode !== 'boundary') || editMode === 'planting') return
    const overlay = L.geoJSON(planting.geometry, {
      pane: 'plantingPane',
      style: {
        color: '#ffffff', weight: 2, fillColor: '#ffffff', fillOpacity: editMode ? .18 : .32, interactive: false,
      },
    }).addTo(map)
    return () => { overlay.remove() }
  }, [map, planting, showPlanting, editMode])

  useEffect(() => {
    if (!map || !drawing || editMode || !points.length) return
    const group = L.layerGroup().addTo(map)
    const latLngs = points.map(([longitude, latitude]) => L.latLng(latitude, longitude))
    L.polyline(latLngs, { color: '#ffffff', weight: 2.5, dashArray: '5 5', interactive: false }).addTo(group)
    for (const point of latLngs) {
      L.circleMarker(point, {
        radius: 4, color: '#0d0d0d', fillColor: '#ffffff', fillOpacity: 1, weight: 1.5, interactive: false,
      }).addTo(group)
    }
    return () => { group.remove() }
  }, [map, drawing, editMode, points])

  useMapGeometryEditor(map, points, { editMode, editLocked, activeVertex, selectVertex, moveVertex, insertVertex })
  useAnalyticsMapLayers(map, selected?.geometry, activeVisualization)

  return (
    <>
      <div ref={host} className={'real-map' + (adding || suggesting ? ' is-drawing' : '')} data-edit-mode={editMode}
        aria-label={t('Interactive geographic map of farm fields')} role="region" />
      <div className="map-controls" aria-label={t('Map controls')}>
        <button className="icon-button" type="button" aria-label={t('Zoom in')} disabled={!map}
          onClick={() => map?.zoomIn()}><PlusIcon aria-hidden="true" /></button>
        <button className="icon-button" type="button" aria-label={t('Zoom out')} disabled={!map}
          onClick={() => map?.zoomOut()}><MinusIcon aria-hidden="true" /></button>
        <button className="icon-button" type="button"
          aria-label={editMode ? t('Fit draft boundary') : selected ? t('Fit selected field') : t('Fit all fields')}
          disabled={!map || (!fields.length && !points.length)}
          onClick={() => {
            if (map) fitFields(map, newBoundary ? [] : selected ? [selected] : fields, editMode ? points : undefined)
          }}><ViewfinderCircleIcon aria-hidden="true" /></button>
      </div>
    </>
  )
}
