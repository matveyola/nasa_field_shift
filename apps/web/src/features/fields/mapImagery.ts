import { useEffect, useEffectEvent } from 'react'
import type { RefObject } from 'react'
import * as L from 'leaflet'
import { coordinateFromMap, placeLabels, preferredAerial } from './basemaps'

const satelliteAttribution = '<a href="https://registry.opendata.aws/sentinel-2-l2a-cogs/">Copernicus Sentinel-2</a>'
const mapTilerKey = import.meta.env.VITE_MAPTILER_KEY?.trim()

export function usePlaceLabels(map: L.Map | undefined, enabled: boolean) {
  useEffect(() => {
    if (!map || !enabled) return
    const labels = L.tileLayer(placeLabels.url, {
      attribution: placeLabels.attribution, maxZoom: placeLabels.maxZoom, zIndex: 4,
      updateWhenIdle: true, keepBuffer: 1,
    }).addTo(map)
    return () => { labels.remove() }
  }, [map, enabled])
}

export function useDetailedImagery(
  map: L.Map | undefined, enabled: boolean, retry: number, visible: RefObject<boolean>, onReady: () => void,
) {
  const signalReady = useEffectEvent(onReady)

  useEffect(() => {
    if (!map || !enabled) return
    const instance = map
    let provider: ReturnType<typeof preferredAerial>
    let tiles: L.TileLayer | undefined
    let logo: L.Control | undefined
    let recent: L.ImageOverlay | undefined
    let active: AbortController | undefined
    let debounce: ReturnType<typeof setTimeout> | undefined
    let tileTimeout: ReturnType<typeof setTimeout> | undefined
    let lastAttempt: string | undefined
    let tilesAvailable = false
    let disposed = false

    function cancelRecent() {
      clearTimeout(debounce)
      if (active) {
        active.abort()
        active = undefined
        lastAttempt = undefined
      }
    }

    function queueRecent() {
      cancelRecent()
      if (instance.getZoom() < 15) return
      const center = instance.getCenter().wrap()
      if (recent?.getBounds().pad(-.2).contains(center)) {
        visible.current = true
        signalReady()
        return
      }
      // Stay within the catalogue's geographic range and avoid repeating
      // a failed request whenever the user nudges the map.
      const position = coordinateFromMap(center)
      if (center.lat < -80 || center.lat > 84) return
      const key = position.map((value) => value.toFixed(2)).join(',')
      if (key === lastAttempt) return

      debounce = setTimeout(async () => {
        const controller = new AbortController()
        active = controller
        lastAttempt = key
        try {
          const { loadSegmentationSatellite } = await import('./segmentationImage')
          const image = await loadSegmentationSatellite(position, controller.signal)
          if (controller.signal.aborted) return
          recent?.remove()
          recent = L.imageOverlay(image.url, image.bounds, {
            pane: 'tilePane', zIndex: 2, interactive: false,
            attribution: satelliteAttribution + (image.acquiredAt ? ' · ' + image.acquiredAt.slice(0, 10) : ''),
            alt: 'Recent Sentinel-2 true-color imagery',
          }).addTo(instance)
          visible.current = true
          signalReady()
        } catch {
          // The cloudless basemap remains visible when no recent scene can be displayed.
        } finally {
          if (active === controller) active = undefined
        }
      }, 900)
    }

    function clearTiles() {
      clearTimeout(tileTimeout)
      // Leaflet unregisters map listeners and attribution from the layer's remove event.
      tiles?.remove().off()
      logo?.remove()
      tiles = undefined
      logo = undefined
      tilesAvailable = false
    }

    function update() {
      const view = instance.getCenter().wrap()
      const next = preferredAerial(coordinateFromMap(view), instance.getZoom(), mapTilerKey)
      if (next !== provider && (next?.bounds || next?.url !== provider?.url)) {
        clearTiles()
        provider = next
        if (next) {
          const options: L.TileLayerOptions = {
            maxNativeZoom: next.maxNativeZoom, maxZoom: 19, zIndex: 1,
            attribution: next.attribution, updateWhenIdle: true, keepBuffer: 1,
            bounds: next.bounds,
          }
          const tileLayer = next.wmsLayers
            ? L.tileLayer.wms(next.url, { ...options, layers: next.wmsLayers, transparent: true, format: 'image/png' })
            : L.tileLayer(next.url, options)
          const logoUrl = next.logo
          tileLayer.on('loading', () => {
            tilesAvailable = false
            visible.current = Boolean(recent?.getBounds().contains(instance.getCenter().wrap()))
            clearTimeout(tileTimeout)
            tileTimeout = setTimeout(queueRecent, 6_000)
          })
            .on('tileload', () => {
              clearTimeout(tileTimeout)
              tilesAvailable = true
              visible.current = true
              signalReady()
              cancelRecent()
              recent?.remove()
              recent = undefined
              if (logoUrl && !logo) {
                const AttributionLogo = L.Control.extend({
                  onAdd: () => {
                    const link = L.DomUtil.create('a', 'map-aerial-brand')
                    link.href = 'https://www.maptiler.com/'
                    const image = L.DomUtil.create('img', '', link)
                    image.src = logoUrl
                    image.alt = 'MapTiler'
                    return link
                  },
                })
                logo = new AttributionLogo({ position: 'bottomleft' }).addTo(instance)
              }
            })
            .on('load', () => {
              clearTimeout(tileTimeout)
              if (!tilesAvailable) queueRecent()
            })
          tiles = tileLayer.addTo(instance)
        }
      }
      if (instance.getZoom() < 15) {
        recent?.remove()
        recent = undefined
        lastAttempt = undefined
      } else if (recent && !instance.getBounds().intersects(recent.getBounds())) {
        recent.remove()
        recent = undefined
      }
      visible.current = tilesAvailable || Boolean(recent?.getBounds().contains(view))
      if (next) {
        if (tilesAvailable) { cancelRecent(); signalReady() }
      }
      else queueRecent()
    }

    function updateAfterMove() {
      // Leaflet can still be dispatching viewreset to the old grid when moveend fires.
      queueMicrotask(() => { if (!disposed) update() })
    }

    update()
    instance.on('movestart', cancelRecent)
    instance.on('moveend', updateAfterMove)
    return () => {
      disposed = true
      instance.off('movestart', cancelRecent)
      instance.off('moveend', updateAfterMove)
      clearTiles()
      cancelRecent()
      recent?.remove()
      visible.current = false
    }
  }, [map, enabled, retry, visible])
}
