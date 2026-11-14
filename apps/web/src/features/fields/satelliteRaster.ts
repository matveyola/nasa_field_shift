import { fromUrl } from 'geotiff'
import proj4 from 'proj4'
import type { SatelliteImage } from '../../api/contracts'

export type SatelliteRaster = {
  url: string
  bounds: [[number, number], [number, number]]
  source?: 'aerial' | 'satellite'
  acquiredAt?: string
  catalogUrl?: string
}

export async function renderSatellite(scene: SatelliteImage, signal: AbortSignal): Promise<SatelliteRaster> {
  const tiff = await fromUrl(scene.visual_url, { allowFullFile: false }, signal)
  const image = await tiff.getImage()
  if (Number(image.getGeoKeys()?.ProjectedCSTypeGeoKey) !== scene.epsg) {
    throw new Error('The scene projection could not be verified.')
  }

  const [originX, originY] = image.getOrigin()
  const [resolutionX, resolutionY] = image.getResolution()
  const zone = scene.epsg % 100
  const hemisphere = scene.epsg >= 32700 ? ' +south' : ''
  const utm = '+proj=utm +zone=' + zone + hemisphere + ' +datum=WGS84 +units=m +no_defs'
  const toImage = proj4('EPSG:3857', utm)
  const toMercator = proj4('EPSG:4326', 'EPSG:3857')
  const [west, south, east, north] = scene.bounds
  const [left, bottom] = toMercator.forward([west, south])
  const [right, top] = toMercator.forward([east, north])

  function sourcePixel(x: number, y: number) {
    const [easting, northing] = toImage.forward([x, y])
    return [(easting - originX) / resolutionX, (northing - originY) / resolutionY]
  }

  const corners = [sourcePixel(left, bottom), sourcePixel(left, top),
    sourcePixel(right, bottom), sourcePixel(right, top)]
  const x = corners.map((point) => point[0])
  const y = corners.map((point) => point[1])
  const firstColumn = Math.max(0, Math.floor(Math.min(...x)) - 2)
  const firstRow = Math.max(0, Math.floor(Math.min(...y)) - 2)
  const lastColumn = Math.min(image.getWidth(), Math.ceil(Math.max(...x)) + 2)
  const lastRow = Math.min(image.getHeight(), Math.ceil(Math.max(...y)) + 2)
  if (lastColumn <= firstColumn || lastRow <= firstRow ||
      lastColumn - firstColumn > 1200 || lastRow - firstRow > 1200) {
    throw new Error('The scene does not contain a readable field window.')
  }

  const rgb = await image.readRGB({
    window: [firstColumn, firstRow, lastColumn, lastRow], interleave: true, signal,
  })
  const canvas = document.createElement('canvas')
  const groundScale = Math.cos((south + north) * Math.PI / 360)
  canvas.width = Math.min(640, Math.max(1, Math.ceil((right - left) * groundScale / 10)))
  canvas.height = Math.min(640, Math.max(1, Math.ceil((top - bottom) * groundScale / 10)))
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Satellite rendering is unavailable.')
  const pixels = context.createImageData(canvas.width, canvas.height)
  let hasPixels = false

  for (let row = 0; row < canvas.height; row += 1) {
    const mapY = top - (row + 0.5) * (top - bottom) / canvas.height
    for (let column = 0; column < canvas.width; column += 1) {
      const mapX = left + (column + 0.5) * (right - left) / canvas.width
      const [sourceColumn, sourceRow] = sourcePixel(mapX, mapY)
      const readColumn = Math.floor(sourceColumn) - firstColumn
      const readRow = Math.floor(sourceRow) - firstRow
      if (readColumn < 0 || readColumn >= rgb.width || readRow < 0 || readRow >= rgb.height) continue
      const readOffset = (readRow * rgb.width + readColumn) * 3
      const [red, green, blue] = [rgb[readOffset], rgb[readOffset + 1], rgb[readOffset + 2]]
      if (red === 0 && green === 0 && blue === 0) continue
      const writeOffset = (row * canvas.width + column) * 4
      pixels.data[writeOffset] = red
      pixels.data[writeOffset + 1] = green
      pixels.data[writeOffset + 2] = blue
      pixels.data[writeOffset + 3] = 255
      hasPixels = true
    }
  }
  if (!hasPixels) throw new Error('The scene has no visible pixels over this field.')
  context.putImageData(pixels, 0, 0)
  return {
    url: canvas.toDataURL(),
    bounds: [[south, west], [north, east]],
    source: 'satellite',
    acquiredAt: scene.acquired_at,
    catalogUrl: scene.catalog_url,
  }
}
