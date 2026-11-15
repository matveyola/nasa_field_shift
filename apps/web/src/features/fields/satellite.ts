import { useCallback, useRef } from 'react'
import { api } from '../../api/client'
import { useMapResource } from './useMapResource'
import type { SatelliteRaster } from './satelliteRaster'

export function useSatelliteLayer(fieldId: string | undefined, enabled: boolean, cutoff: string, boundaryRevision: number) {
  const previous = useRef<{ key: string; raster: SatelliteRaster } | undefined>(undefined)
  const load = useCallback(async (signal: AbortSignal) => {
    if (!enabled || !fieldId) return undefined
    const scene = await api.satellite(fieldId, cutoff, signal)
    if (!scene) return { fieldId, cutoff, boundaryRevision, scene: null, raster: undefined }
    const key = [scene.visual_url, scene.epsg, ...scene.bounds].join('|')
    if (previous.current?.key === key) return { fieldId, cutoff, boundaryRevision, scene, raster: previous.current.raster }
    try {
      const { renderSatellite } = await import('./satelliteRaster')
      const raster = await renderSatellite(scene, AbortSignal.any([signal, AbortSignal.timeout(20_000)]))
      if (!signal.aborted) previous.current = { key, raster }
      return { fieldId, cutoff, boundaryRevision, scene, raster }
    } catch (error) {
      if (signal.aborted) throw error
      throw new Error('The Sentinel-2 image could not be loaded. Try again or choose another date.')
    }
  }, [fieldId, enabled, cutoff, boundaryRevision])
  const resource = useMapResource(load, enabled && !cutoff ? 15 * 60 * 1000 : undefined)
  const data = enabled && resource.data?.fieldId === fieldId && resource.data?.cutoff === cutoff
    && resource.data?.boundaryRevision === boundaryRevision
    ? resource.data : undefined
  return { ...resource, data }
}
