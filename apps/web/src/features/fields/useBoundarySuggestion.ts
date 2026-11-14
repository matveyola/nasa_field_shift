import { useEffect, useRef, useState } from 'react'
import type { Position } from '../../api/contracts'
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
  source: ImageWindow['source']
  preview?: SatelliteRaster
  points: Position[]
}

type Search = { seed?: Position; pending?: 'boundary' | 'imagery'; error?: string; preview?: SatelliteRaster }

export function useBoundarySuggestion() {
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

  function begin() {
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    const timeout = AbortSignal.timeout(25_000)
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
    const { controller, timeout, signal } = begin()
    let preview = search?.preview
    setSearch({ seed: position, pending: 'boundary', preview })
    try {
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
        error: timeout.aborted ? 'The boundary search timed out. Try again.' : errorText(error),
      })
    } finally {
      if (active.current === controller) active.current = undefined
    }
  }

  return { search, open, close, find, showSatellite }
}
