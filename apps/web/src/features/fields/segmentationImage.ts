import { api } from '../../api/client'
import type { Position } from '../../api/contracts'
import { defaultBasemap, demoAerialBounds, usgsImageryService } from './basemaps'
import type { SatelliteRaster } from './satelliteRaster'

type Point = [number, number]
type Frame = { left: number; bottom: number; right: number; top: number }

export type SegmentationImage = {
  width: number
  height: number
  pixels: Uint8ClampedArray
  seed: Point
  coordinateAt: (point: Point) => Position
  source: 'aerial' | 'satellite'
  preview?: SatelliteRaster
}

const earthRadius = 6_378_137
const mercatorLimit = earthRadius * Math.PI
const radians = Math.PI / 180
const windowRadiusMeters = 1_200
const maxSide = 512
const maxImageBytes = 8 * 1024 * 1024
const noImagery = 'No usable imagery was available around this point.'
const failedImage = 'The image could not be loaded. Try again.'

function project([longitude, latitude]: Position): Point {
  return [earthRadius * longitude * radians, earthRadius * Math.asinh(Math.tan(latitude * radians))]
}

function unproject([x, y]: Point): Position {
  return [x / earthRadius / radians, Math.atan(Math.sinh(y / earthRadius)) / radians]
}

function frameForBounds([[south, west], [north, east]]: SatelliteRaster['bounds']): Frame {
  const [left, bottom] = project([west, south])
  const [right, top] = project([east, north])
  return { left, bottom, right, top }
}

function windowAround([x, y]: Point, latitude: number): Frame {
  const radius = windowRadiusMeters / Math.cos(latitude * radians)
  return { left: x - radius, bottom: y - radius, right: x + radius, top: y + radius }
}

function contains({ left, bottom, right, top }: Frame, [x, y]: Point) {
  return left < x && x < right && bottom < y && y < top
}

function validPosition([longitude, latitude]: Position) {
  return Math.abs(longitude) <= 180 && latitude >= -80 && latitude <= 84
}

export async function loadSegmentationSatellite(position: Position, signal: AbortSignal): Promise<SatelliteRaster> {
  if (!validPosition(position)) throw new Error(noImagery)
  const bounded = AbortSignal.any([signal, AbortSignal.timeout(20_000)])
  try {
    const scene = await api.satelliteAt(position, bounded)
    if (!scene) throw new Error(noImagery)
    const { renderSatellite } = await import('./satelliteRaster')
    return await renderSatellite(scene, bounded)
  } catch (error) {
    signal.throwIfAborted()
    if (error instanceof Error && error.message === noImagery) throw error
    throw new Error(failedImage)
  }
}

async function extractWindow(
  url: string,
  frame: Frame,
  window: Frame,
  position: Point,
  source: SegmentationImage['source'],
  signal: AbortSignal,
  preview?: SatelliteRaster,
): Promise<SegmentationImage> {
  const { left, right, bottom, top } = frame
  const clipped: Frame = {
    left: Math.max(left, window.left),
    bottom: Math.max(bottom, window.bottom),
    right: Math.min(right, window.right),
    top: Math.min(top, window.top),
  }
  if (!contains(clipped, position)) throw new Error(noImagery)

  const response = await fetch(url, { signal, credentials: 'omit' })
  if (!response.ok || Number(response.headers.get('content-length')) > maxImageBytes) {
    throw new Error(failedImage)
  }
  const image = await response.blob()
  if (image.size > maxImageBytes || !/^image\/(png|jpeg|webp)$/.test(image.type)) throw new Error(failedImage)
  const bitmap = await createImageBitmap(image)
  try {
    signal.throwIfAborted()
    if (bitmap.width > 2048 || bitmap.height > 2048) throw new Error(failedImage)
    const scaleX = bitmap.width / (right - left)
    const scaleY = bitmap.height / (top - bottom)
    const cropWidth = (clipped.right - clipped.left) * scaleX
    const cropHeight = (clipped.top - clipped.bottom) * scaleY
    // Downsample only, and use one factor so a short clipped edge is not stretched.
    const scale = Math.min(1, maxSide / Math.max(cropWidth, cropHeight))
    const width = Math.floor(cropWidth * scale + 1e-8)
    const height = Math.floor(cropHeight * scale + 1e-8)
    if (width < 24 || height < 24) throw new Error(noImagery)
    const metersPerPixelX = (clipped.right - clipped.left) / width
    const metersPerPixelY = (clipped.top - clipped.bottom) / height
    const seed: Point = [(position[0] - clipped.left) / metersPerPixelX, (clipped.top - position[1]) / metersPerPixelY]
    const margin = 6
    if (seed[0] < margin || seed[0] >= width - margin
      || seed[1] < margin || seed[1] >= height - margin) throw new Error(noImagery)

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) throw new Error(failedImage)
    context.imageSmoothingQuality = 'high'
    context.drawImage(bitmap, (clipped.left - left) * scaleX, (top - clipped.top) * scaleY,
      cropWidth, cropHeight, 0, 0, width, height)
    const pixels = context.getImageData(0, 0, width, height).data
    if (pixels[(Math.floor(seed[1]) * width + Math.floor(seed[0])) * 4 + 3] < 240) throw new Error(noImagery)
    if (!preview && source === 'aerial') {
      const [west, south] = unproject([clipped.left, clipped.bottom])
      const [east, north] = unproject([clipped.right, clipped.top])
      preview = { url: canvas.toDataURL(), bounds: [[south, west], [north, east]], source }
    }
    return {
      width, height, pixels, seed, source, preview,
      coordinateAt: ([x, y]) => unproject([clipped.left + x * metersPerPixelX, clipped.top - y * metersPerPixelY]),
    }
  } finally {
    bitmap.close()
  }
}

function usgsUrl({ left, bottom, right, top }: Frame) {
  const params = new URLSearchParams({
    bbox: [left, bottom, right, top].join(','),
    bboxSR: '3857', imageSR: '3857', size: maxSide + ',' + maxSide, format: 'png32', transparent: 'true', f: 'image',
  })
  return usgsImageryService + '/export?' + params
}

export async function readSegmentationImage(
  position: Position,
  { satellite, signal }: { satellite?: SatelliteRaster; signal: AbortSignal },
): Promise<SegmentationImage> {
  if (!validPosition(position)) throw new Error(noImagery)
  const point = project(position)
  const window = windowAround(point, position[1])
  function fromPreview(raster: SatelliteRaster) {
    const source = raster.source ?? 'satellite'
    return extractWindow(raster.url, frameForBounds(raster.bounds), window, point, source, signal, raster)
  }
  if (satellite && contains(frameForBounds(satellite.bounds), point)) {
    try { return await fromPreview(satellite) }
    catch { signal.throwIfAborted() }
  }
  const local = frameForBounds(demoAerialBounds)
  const bounded = AbortSignal.any([signal, AbortSignal.timeout(8_000)])
  if (contains(local, point)) {
    try { return await extractWindow('/bondurant-usgs-aerial.webp', local, window, point, 'aerial', bounded) }
    catch { signal.throwIfAborted() }
  }
  if (defaultBasemap(position) === 'aerial' && window.left >= -mercatorLimit && window.right <= mercatorLimit) {
    try { return await extractWindow(usgsUrl(window), window, window, point, 'aerial', bounded) }
    catch { signal.throwIfAborted() }
  }
  try {
    return await fromPreview(await loadSegmentationSatellite(position, signal))
  } catch (error) {
    signal.throwIfAborted()
    if (error instanceof Error && error.message === noImagery) throw error
    throw new Error(failedImage)
  }
}
