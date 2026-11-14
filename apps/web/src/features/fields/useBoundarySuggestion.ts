import { useEffect, useRef, useState } from 'react'
import { request } from '../../api/client'
import type { Polygon, Position } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { errorText } from '../../shared/useResource'
import { polygonFrom } from './geometry'
import type { SatelliteRaster } from './satelliteRaster'
import type { readSegmentationImage } from './segmentationImage'

type ImageWindow = Awaited<ReturnType<typeof readSegmentationImage>>
type PixelRing = [number, number][]

function runSegmentation({ width, height, pixels, seed, source }: ImageWindow, signal: AbortSignal) {
  signal.throwIfAborted()
  return new Promise<PixelRing | null>((resolve, reject) => {
    const worker = new Worker(new URL('./segmentation.worker.ts', import.meta.url), { type: 'module' })
    function finish() {
      signal.removeEventListener('abort', abort)
      worker.terminate()
    }
    function abort() {
      finish()
      reject(signal.reason)
    }
    worker.onmessage = (event: MessageEvent<PixelRing | null>) => {
      finish()
      resolve(event.data)
    }
    worker.onerror = (event) => {
      event.preventDefault()
      finish()
      reject(new Error('The boundary search failed. Try again.'))
    }
    signal.addEventListener('abort', abort, { once: true })
    try {
      worker.postMessage({ width, height, pixels, seed, source }, [pixels.buffer])
    } catch (error) {
      finish()
      reject(error)
    }
  })
}

export type BoundarySuggestion = {
  source: ImageWindow['source'] | 'model'
  imageryDate?: string
  imagerySource?: string
  preview?: SatelliteRaster
  points: Position[]
}

type Search = { seed?: Position; pending?: 'boundary' | 'imagery'; error?: string; preview?: SatelliteRaster }

const missingContour = 'Delineate Anything v2 could not separate this field. Tap nearer the middle or draw it manually.'
const modelErrors: Record<string, string> = {
  no_imagery: 'No usable imagery loaded for this point. Try again or draw the field manually.',
  no_contour: missingContour,
  model_unavailable: 'The boundary model is temporarily unavailable. Try again in a moment.',
  busy: 'The boundary model is still processing a request. Try again in a moment.',
}

export function useBoundarySuggestion() {
  const { t } = useLocale()
  const [search, setSearch] = useState<Search>()
  const active = useRef<AbortController | undefined>(undefined)

  useEffect(() => () => active.current?.abort(), [])

  function open() {
    setSearch({})
  }

  function close() {
    active.current?.abort()
    active.current = undefined
    setSearch(undefined)
  }

  function begin(timeoutMs = 25_000) {
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    const timeout = AbortSignal.timeout(timeoutMs)
    const signal = AbortSignal.any([controller.signal, timeout])
    return { controller, timeout, signal }
  }

  async function showSatellite(position: Position) {
    const { controller, signal } = begin()
    setSearch((current) => ({ preview: current?.preview, pending: 'imagery' }))
    try {
      const { loadSegmentationSatellite } = await import('./segmentationImage')
      const preview = await loadSegmentationSatellite(position, signal)
      if (!controller.signal.aborted) setSearch({ preview })
    } catch (error) {
      if (!controller.signal.aborted) setSearch((current) => ({ preview: current?.preview, error: errorText(error) }))
    } finally {
      if (active.current === controller) active.current = undefined
    }
  }

  async function find(position: Position, satellite?: SatelliteRaster): Promise<BoundarySuggestion | undefined> {
    const { controller, timeout, signal } = begin(55_000)
    let preview = search?.preview
    setSearch({ seed: position, pending: 'boundary', preview })
    try {
      const model = await request<{
        geometry: Polygon | null; imagery_date: string | null; imagery_source: string | null
        failure_reason?: string | null
      } | null>(
        '/fields/boundary-suggestion', { method: 'POST', body: JSON.stringify({ position }), signal },
      )
      signal.throwIfAborted()
      if (model !== null) {
        if (!model.geometry) throw new Error(t(modelErrors[model.failure_reason ?? ''] ?? missingContour))
        const points = polygonFrom(model.geometry.coordinates[0]).coordinates[0].slice(0, -1)
        setSearch(undefined)
        return { points, source: 'model', imageryDate: model.imagery_date ?? undefined,
          imagerySource: model.imagery_source ?? undefined }
      }

      // The browser algorithm supports deployments that explicitly disable the model.
      const { readSegmentationImage } = await import('./segmentationImage')
      const image = await readSegmentationImage(position, { satellite: preview ?? satellite, signal })
      preview = image.preview ?? preview
      const ring = await runSegmentation(image, signal)
      if (controller.signal.aborted) return
      if (!ring) throw new Error('No clear field boundary was found. Try clicking farther inside the field.')
      const points = polygonFrom(ring.map(image.coordinateAt)).coordinates[0].slice(0, -1)
      setSearch(undefined)
      return { points, source: image.source, preview: image.preview }
    } catch (error) {
      if (!controller.signal.aborted) setSearch({
        seed: position, preview,
        error: timeout.aborted ? t('The boundary search timed out. Try again.') : errorText(error),
      })
    } finally {
      if (active.current === controller) active.current = undefined
    }
  }

  return { search, open, close, find, showSatellite }
}
