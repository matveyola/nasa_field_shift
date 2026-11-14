import { fromUrl } from 'geotiff'
import proj4 from 'proj4'
import type { SegmentationImage } from './segmentationImage'

type Grid = Pick<SegmentationImage, 'width' | 'height' | 'coordinateAt'>
type SceneAsset = { url: string; scale: number; offset: number; nodata: number }
type ProjectedGrid = { east: Float64Array; north: Float64Array }

export type SentinelSpectralFrame = {
  id: string
  acquiredAt: string
  nir: Float32Array
  ndvi: Float32Array
  valid: Uint8Array
}

const assetHosts = new Set([
  'sentinel-cogs.s3.us-west-2.amazonaws.com',
  'e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com',
])
const maximumSide = 640
const maximumReadSide = 1200

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object') throw new Error('Invalid Sentinel-2 item metadata.')
  return value as Record<string, unknown>
}

function readAsset(value: unknown, id: string, file: string, offsetApplied: boolean): SceneAsset {
  const asset = record(value)
  if (typeof asset.href !== 'string') throw new Error('Missing Sentinel-2 band URL.')
  const url = new URL(asset.href)
  if (url.protocol !== 'https:' || !assetHosts.has(url.host) || url.username || url.password || url.search
    || url.hash || !url.pathname.endsWith('/' + id + '/' + file)) {
    throw new Error('Unrecognized Sentinel-2 band URL.')
  }

  const bands = asset['raster:bands']
  const band = record(Array.isArray(bands) ? bands[0] : undefined)
  if (file === 'SCL.tif') return { url: url.href, scale: 1, offset: 0, nodata: 0 }
  const scale = band.scale
  const offset = offsetApplied ? 0 : band.offset ?? 0
  if (typeof scale !== 'number' || !Number.isFinite(scale) || scale <= 0
    || typeof offset !== 'number' || !Number.isFinite(offset)) {
    throw new Error('Missing Sentinel-2 reflectance calibration.')
  }
  const nodata = typeof band.nodata === 'number' ? band.nodata : 0
  return { url: url.href, scale, offset, nodata }
}

function projectGrid(grid: Grid, toUtm: proj4.Converter): ProjectedGrid {
  const { width, height } = grid
  const mercator = proj4('EPSG:4326', 'EPSG:3857')
  const [left, top] = mercator.forward(grid.coordinateAt([0, 0]))
  const [right, bottom] = mercator.forward(grid.coordinateAt([width, height]))
  if (![left, top, right, bottom].every(Number.isFinite) || right <= left || top <= bottom) {
    throw new Error('Invalid Sentinel-2 output grid bounds.')
  }
  const east = new Float64Array(width * height)
  const north = new Float64Array(east.length)
  for (let row = 0; row < height; row += 1) {
    const y = top - (row + 0.5) * (top - bottom) / height
    for (let col = 0; col < width; col += 1) {
      const x = left + (col + 0.5) * (right - left) / width
      const [easting, northing] = toUtm.forward([x, y])
      const index = row * width + col
      east[index] = easting
      north[index] = northing
    }
  }
  return { east, north }
}

async function readBand(asset: SceneAsset, epsg: number, grids: ProjectedGrid[], signal: AbortSignal) {
  const tiff = await fromUrl(asset.url, { allowFullFile: false }, signal)
  const image = await tiff.getImage()
  const keys = image.getGeoKeys()
  const [originX, originY] = image.getOrigin()
  const [stepX, stepY] = image.getResolution()
  if (Number(keys?.ProjectedCSTypeGeoKey) !== epsg || keys?.GTRasterTypeGeoKey === 2 || image.getSamplesPerPixel() !== 1
    || ![originX, originY, stepX, stepY].every(Number.isFinite) || stepX <= 0 || stepY >= 0) {
    throw new Error('Unexpected Sentinel-2 band grid.')
  }

  const width = image.getWidth()
  const height = image.getHeight()
  let left = width, top = height, right = 0, bottom = 0
  for (const { east, north } of grids) {
    for (let i = 0; i < east.length; i += 1) {
      const col = Math.floor((east[i] - originX) / stepX)
      const row = Math.floor((north[i] - originY) / stepY)
      if (col < 0 || col >= width || row < 0 || row >= height) continue
      left = Math.min(left, col)
      top = Math.min(top, row)
      right = Math.max(right, col + 1)
      bottom = Math.max(bottom, row + 1)
    }
  }
  if (left >= right || top >= bottom || right - left > maximumReadSide || bottom - top > maximumReadSide) {
    throw new Error('Sentinel-2 band window is unavailable or too large.')
  }

  const raster = await image.readRasters({ samples: [0], interleave: true, window: [left, top, right, bottom], signal })
  return function sample(east: number, north: number): number {
    const col = Math.floor((east - originX) / stepX)
    const row = Math.floor((north - originY) / stepY)
    if (col < left || col >= right || row < top || row >= bottom) return NaN
    const dn = raster[(row - top) * raster.width + col - left]
    return dn === asset.nodata || dn === 0 ? NaN : dn * asset.scale + asset.offset
  }
}

/** Sample each nearby RGB window on its exact pixel centers while sharing each band read. */
export async function renderSentinelSpectralBatch(item: unknown, grids: Grid[], signal: AbortSignal): Promise<SentinelSpectralFrame[]> {
  if (!grids.length || grids.some(({ width, height }) => ![width, height].every(
    (side) => Number.isInteger(side) && side >= 1 && side <= maximumSide,
  )) || grids.reduce((sum, { width, height }) => sum + width * height, 0) > 4 * maximumSide ** 2) {
    throw new Error('Sentinel-2 output grid batch is too large.')
  }
  const scene = record(item)
  const properties = record(scene.properties)
  const id = scene.id
  const acquiredAt = properties.datetime
  const epsg = Number(properties['proj:epsg'] ?? String(properties['proj:code']).replace(/^EPSG:/, ''))
  const zone = epsg % 100
  const hemisphere = Math.floor(epsg / 100)
  if (scene.collection !== 'sentinel-2-l2a' || typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,160}$/.test(id)
    || typeof acquiredAt !== 'string' || !Number.isFinite(Date.parse(acquiredAt)) || !Number.isInteger(epsg)
    || (hemisphere !== 326 && hemisphere !== 327) || zone < 1 || zone > 60) {
    throw new Error('Unsupported Sentinel-2 scene metadata.')
  }
  const assets = record(scene.assets)
  const offsetApplied = properties['earthsearch:boa_offset_applied'] === true
  const redAsset = readAsset(assets.red, id, 'B04.tif', offsetApplied)
  const nirAsset = readAsset(assets.nir, id, 'B08.tif', offsetApplied)
  const sclAsset = readAsset(assets.scl, id, 'SCL.tif', false)
  const utm = '+proj=utm +zone=' + zone + (hemisphere === 327 ? ' +south' : '') + ' +datum=WGS84 +units=m +no_defs'
  const toUtm = proj4('EPSG:3857', utm)
  const projected = grids.map((grid) => projectGrid(grid, toUtm))
  signal.throwIfAborted()
  const [redAt, nirAt, sclAt] = await Promise.all([
    readBand(redAsset, epsg, projected, signal),
    readBand(nirAsset, epsg, projected, signal),
    readBand(sclAsset, epsg, projected, signal),
  ])

  return projected.map(({ east, north }) => {
    const nir = new Float32Array(east.length).fill(NaN)
    const ndvi = new Float32Array(east.length).fill(NaN)
    const valid = new Uint8Array(east.length)
    for (let i = 0; i < east.length; i += 1) {
      const classification = sclAt(east[i], north[i])
      // Keep clear vegetation, bare ground and water. Cloud, shadow, snow and
      // unclassified pixels cannot supply reliable edge evidence.
      if (classification !== 4 && classification !== 5 && classification !== 6) continue
      const red = redAt(east[i], north[i])
      const infrared = nirAt(east[i], north[i])
      if (!(red >= 0 && infrared >= 0 && red + infrared > 0)) continue
      nir[i] = infrared
      ndvi[i] = (infrared - red) / (infrared + red)
      valid[i] = 1
    }
    return { id, acquiredAt, nir, ndvi, valid }
  })
}

export async function renderSentinelSpectral(item: unknown, grid: Grid, signal: AbortSignal): Promise<SentinelSpectralFrame> {
  const [frame] = await renderSentinelSpectralBatch(item, [grid], signal)
  return frame
}
