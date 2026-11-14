type PixelPoint = [number, number]
type Bounds = { left: number; right: number; top: number; bottom: number }
type EdgeBarriers = { horizontal: Uint8Array; vertical: Uint8Array }

export type SpectralFrame = {
  /** Row-major B08 surface reflectance, with unavailable pixels set to NaN. */
  nir: Float32Array
  /** Row-major NDVI from surface reflectance, with unavailable pixels set to NaN. */
  ndvi: Float32Array
}

export type FieldSegmentationInput = {
  width: number
  height: number
  pixels: Uint8ClampedArray
  seed: PixelPoint
  source?: 'aerial' | 'satellite'
  /** The anchor date first, then up to three other aligned, cloud-masked dates. */
  spectralFrames?: readonly SpectralFrame[]
}

const minimumSize = 24
const maximumSize = 640
const maxVertices = 127
const tolerances = [8, 10, 12, 14, 16, 19, 23, 27, 32, 38]
const satelliteTolerances = [4, 6, ...tolerances]
const maxSpectralDates = 4

type SpectralFeatures = {
  raw: [Float32Array, Float32Array]
  channels: [Float32Array, Float32Array]
  center: [number, number]
  edgeThreshold: number
}

function boxMean(input: Float32Array, width: number, height: number, radius: number) {
  const result = new Float32Array(input.length)
  const stride = width + 1
  const integral = new Float64Array(stride * (height + 1))
  for (let y = 0; y < height; y += 1) {
    let rowTotal = 0
    for (let x = 0; x < width; x += 1) {
      rowTotal += input[y * width + x]
      integral[(y + 1) * stride + x + 1] = rowTotal + integral[y * stride + x + 1]
    }
  }
  for (let y = 0; y < height; y += 1) {
    const top = Math.max(0, y - radius)
    const bottom = Math.min(height, y + radius + 1)
    for (let x = 0; x < width; x += 1) {
      const left = Math.max(0, x - radius)
      const right = Math.min(width, x + radius + 1)
      const total = integral[bottom * stride + right] - integral[top * stride + right]
        - integral[bottom * stride + left] + integral[top * stride + left]
      result[y * width + x] = total / ((right - left) * (bottom - top))
    }
  }
  return result
}

function imageFeatures(width: number, height: number, pixels: Uint8ClampedArray, radius: number) {
  const length = width * height
  const brightness = new Float32Array(length)
  const squared = new Float32Array(length)
  const vegetation = new Float32Array(length)
  const soil = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    const r = pixels[4 * i]
    const g = pixels[4 * i + 1]
    const b = pixels[4 * i + 2]
    brightness[i] = .299 * r + .587 * g + .114 * b
    squared[i] = brightness[i] ** 2
    vegetation[i] = 1.65 * (g - r)
    soil[i] = .85 * (r - b)
  }
  const mean = boxMean(brightness, width, height, radius)
  const variance = boxMean(squared, width, height, radius)
  for (let i = 0; i < length; i += 1) variance[i] = 1.5 * Math.sqrt(Math.max(0, variance[i] - mean[i] ** 2))
  return [mean, boxMean(vegetation, width, height, radius), boxMean(soil, width, height, radius),
    boxMean(variance, width, height, radius)]
}

function seedFeatures(channels: Float32Array[], width: number, height: number, x: number, y: number) {
  return channels.map((channel) => {
    const sample: number[] = []
    const radius = Math.min(width, height) < 180 ? 2 : 3
    for (let row = y - radius; row <= y + radius; row += 1) {
      for (let column = x - radius; column <= x + radius; column += 1) sample.push(channel[row * width + column])
    }
    sample.sort((a, b) => a - b)
    return sample[sample.length >> 1]
  })
}

function rgbDifference(pixels: Uint8ClampedArray, a: number, b: number) {
  return ((pixels[4 * b] - pixels[4 * a]) ** 2 + (pixels[4 * b + 1] - pixels[4 * a + 1]) ** 2
    + (pixels[4 * b + 2] - pixels[4 * a + 2]) ** 2) / 3
}

function bridgeBarriers(walls: Uint8Array, start: number, along: number, length: number, maxGap: number) {
  // Repair short gaps only between runs with several supporting pixels on both sides.
  let previousEnd = -1
  for (let pos = 0; pos < length;) {
    if (!walls[start + pos * along]) {
      pos += 1
      continue
    }
    const runStart = pos
    while (pos < length && walls[start + pos * along]) pos += 1
    if (pos - runStart < 3) continue
    if (previousEnd >= 0 && runStart - previousEnd <= maxGap) {
      for (let gap = previousEnd; gap < runStart; gap += 1) walls[start + gap * along] = 1
    }
    previousEnd = pos
  }
}

function coherentBarriers(
  width: number, height: number, pixels: Uint8ClampedArray, valid: Uint8Array, seedX: number, seedY: number,
): EdgeBarriers {
  const localDifferences: number[] = []
  for (let y = seedY - 3; y <= seedY + 3; y += 1) {
    for (let x = seedX - 3; x <= seedX + 3; x += 1) {
      const i = y * width + x
      localDifferences.push(rgbDifference(pixels, i, i + 1), rgbDifference(pixels, i, i + width))
    }
  }
  localDifferences.sort((a, b) => a - b)
  const noise = Math.sqrt(localDifferences[localDifferences.length >> 1])
  const minimumDot = Math.min(18, Math.max(6, 2.5 * noise + 2)) ** 2 * 3

  function build(step: number, along: number, strips: number, length: number) {
    const walls = new Uint8Array(width * height)
    for (let strip = 0; strip < strips; strip += 1) {
      const start = strip * step
      for (let pos = 2; pos < length - 2; pos += 1) {
        const i = start + pos * along
        if (!valid[i] || !valid[i + step]) continue
        const r = pixels[4 * (i + step)] - pixels[4 * i]
        const g = pixels[4 * (i + step) + 1] - pixels[4 * i + 1]
        const b = pixels[4 * (i + step) + 2] - pixels[4 * i + 2]
        if (r * r + g * g + b * b < minimumDot) continue
        let support = 0
        for (let offset = -2; offset <= 2; offset += 1) {
          const a = i + offset * along
          const n = a + step
          if (!valid[a] || !valid[n]) continue
          const dot = r * (pixels[4 * n] - pixels[4 * a]) + g * (pixels[4 * n + 1] - pixels[4 * a + 1])
            + b * (pixels[4 * n + 2] - pixels[4 * a + 2])
          if (dot >= minimumDot * .65) support += 1
        }
        if (support >= 3) walls[i] = 1
      }

      bridgeBarriers(walls, start, along, length, 8)
    }
    return walls
  }

  return { horizontal: build(1, width, width - 1, height), vertical: build(width, 1, height - 1, width) }
}

function median(values: number[]) {
  values.sort((a, b) => a - b)
  return values[values.length >> 1]
}

function smoothMasked(input: Float32Array, width: number, height: number) {
  const output = input.slice()
  const offsets = [-1, 1, -width, width]
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x
      if (!Number.isFinite(input[i])) continue
      let sum = input[i] * 2
      let count = 2
      for (const offset of offsets) {
        const neighbor = input[i + offset]
        if (!Number.isFinite(neighbor)) continue
        sum += neighbor
        count += 1
      }
      output[i] = sum / count
    }
  }
  return output
}

function prepareSpectralFrames(
  width: number, height: number, frames: readonly SpectralFrame[], seedX: number, seedY: number,
): SpectralFeatures[] {
  const output: SpectralFeatures[] = []
  const size = width * height
  for (const { nir, ndvi } of frames.slice(0, maxSpectralDates)) {
    if (nir.length !== size || ndvi.length !== size) continue
    // Scale reflectance and NDVI to roughly the same feature units as the RGB differences.
    const nearInfrared = new Float32Array(size)
    const vegetation = new Float32Array(size)
    for (let i = 0; i < size; i += 1) {
      const valid = nir[i] >= -.1 && nir[i] <= 1.5 && Math.abs(ndvi[i]) <= 1
      nearInfrared[i] = valid ? 100 * nir[i] : NaN
      vegetation[i] = valid ? 45 * ndvi[i] : NaN
    }
    const seed = seedY * width + seedX
    if (!Number.isFinite(nearInfrared[seed])) continue
    const seedNir: number[] = []
    const seedNdvi: number[] = []
    const noise: number[] = []
    for (let y = seedY - 2; y <= seedY + 2; y += 1) {
      for (let x = seedX - 2; x <= seedX + 2; x += 1) {
        const i = y * width + x
        if (!Number.isFinite(nearInfrared[i])) continue
        seedNir.push(nearInfrared[i])
        seedNdvi.push(vegetation[i])
        for (const j of [i + 1, i + width]) {
          if (!Number.isFinite(nearInfrared[j])) continue
          noise.push(Math.hypot(nearInfrared[j] - nearInfrared[i], vegetation[j] - vegetation[i]))
        }
      }
    }
    if (seedNir.length < 19 || noise.length < 12) continue
    output.push({
      raw: [nearInfrared, vegetation],
      channels: [smoothMasked(nearInfrared, width, height), smoothMasked(vegetation, width, height)],
      center: [median(seedNir), median(seedNdvi)],
      edgeThreshold: Math.min(13, Math.max(4.5, 2 * median(noise) + 1.5)),
    })
  }
  return output
}

function addSpectralBarriers(barriers: EdgeBarriers, frames: SpectralFeatures[], width: number, height: number) {
  function add(walls: Uint8Array, step: number, along: number, strips: number, length: number) {
    const spectralWalls = new Uint8Array(walls.length)
    for (let strip = 0; strip < strips; strip += 1) {
      const start = strip * step
      for (let pos = 2; pos < length - 2; pos += 1) {
        const i = start + pos * along
        let weakVotes = 0
        for (const { raw: [nir, ndvi], edgeThreshold } of frames) {
          const dn = nir[i + step] - nir[i]
          const dv = ndvi[i + step] - ndvi[i]
          const strength = dn * dn + dv * dv
          const limit = edgeThreshold ** 2
          // Different crops may switch which side is greener over time. Require a
          // consistent edge direction locally within each date, not the same sign between dates.
          if (!(strength >= limit * .64)) continue
          let support = 0
          for (let offset = -2; offset <= 2; offset += 1) {
            const j = i + offset * along
            const dot = dn * (nir[j + step] - nir[j]) + dv * (ndvi[j + step] - ndvi[j])
            if (dot >= limit * .52) support += 1
          }
          if (support < 3) continue
          if (strength >= limit * (frames.length === 1 ? 1 : 1.96)) {
            spectralWalls[i] = 1
            break
          }
          weakVotes += 1
        }
        if (weakVotes >= 2) spectralWalls[i] = 1
      }
      bridgeBarriers(spectralWalls, start, along, length, 5)
    }
    for (let i = 0; i < walls.length; i += 1) walls[i] |= spectralWalls[i]
  }

  add(barriers.horizontal, 1, width, width - 1, height)
  add(barriers.vertical, width, 1, height - 1, width)
}

function addSpectralGradient(gradient: Float32Array, frames: SpectralFeatures[], width: number, height: number) {
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x
      let peak = 0
      for (const { channels: [nir, ndvi] } of frames) {
        if (!Number.isFinite(nir[i])) continue
        const horizontal = (nir[i + 1] - nir[i - 1]) ** 2 + (ndvi[i + 1] - ndvi[i - 1]) ** 2
        const vertical = (nir[i + width] - nir[i - width]) ** 2 + (ndvi[i + width] - ndvi[i - width]) ** 2
        peak = Math.max(peak, (horizontal || 0) + (vertical || 0))
      }
      gradient[i] = Math.hypot(gradient[i], Math.min(12, .45 * Math.sqrt(peak)))
    }
  }
}

function component(
  mask: Uint8Array, seed: number, width: number, height: number, queue: Int32Array, barriers?: EdgeBarriers,
) {
  const selected = new Uint8Array(mask.length)
  const bounds = { left: width, right: 0, top: height, bottom: 0 }
  if (mask[seed] === 0) return { selected, size: 0, bounds }
  let size = 0
  function add(index: number) {
    if (mask[index] && !selected[index]) {
      selected[index] = 1
      queue[size++] = index
    }
  }
  add(seed)
  for (let next = 0; next < size; next += 1) {
    const index = queue[next]
    const x = index % width
    const y = Math.floor(index / width)
    bounds.left = Math.min(bounds.left, x)
    bounds.right = Math.max(bounds.right, x)
    bounds.top = Math.min(bounds.top, y)
    bounds.bottom = Math.max(bounds.bottom, y)
    if (x > 0 && !barriers?.horizontal[index - 1]) add(index - 1)
    if (x < width - 1 && !barriers?.horizontal[index]) add(index + 1)
    if (index >= width && !barriers?.vertical[index - width]) add(index - width)
    if (index < width * (height - 1) && !barriers?.vertical[index]) add(index + width)
  }
  return { selected, size, bounds }
}

function morphology(mask: Uint8Array, width: number, height: number, radius: number, expand: boolean) {
  const result = new Uint8Array(mask.length)
  const stride = width + 1
  const integral = new Int32Array(stride * (height + 1))
  for (let y = 0; y < height; y += 1) {
    let rowTotal = 0
    for (let x = 0; x < width; x += 1) {
      rowTotal += mask[y * width + x]
      integral[(y + 1) * stride + x + 1] = rowTotal + integral[y * stride + x + 1]
    }
  }
  const kernelArea = (2 * radius + 1) ** 2
  for (let y = 0; y < height; y += 1) {
    const top = Math.max(0, y - radius)
    const bottom = Math.min(height, y + radius + 1)
    for (let x = 0; x < width; x += 1) {
      const left = Math.max(0, x - radius)
      const right = Math.min(width, x + radius + 1)
      const total = integral[bottom * stride + right] - integral[top * stride + right]
        - integral[bottom * stride + left] + integral[top * stride + left]
      result[y * width + x] = Number(expand ? total > 0 : total === kernelArea)
    }
  }
  return result
}

function fillHoles(mask: Uint8Array, width: number, bounds: Bounds, queue: Int32Array) {
  // Temporarily mark exterior background as 2. Four-connected foreground needs an
  // eight-connected exterior so a diagonal corner does not create a phantom hole.
  let size = 0
  function mark(index: number) {
    if (mask[index] === 0) {
      mask[index] = 2
      queue[size++] = index
    }
  }
  const { left, right, top, bottom } = bounds
  for (let x = left; x <= right; x += 1) {
    mark(top * width + x)
    mark(bottom * width + x)
  }
  for (let y = top + 1; y < bottom; y += 1) {
    mark(y * width + left)
    mark(y * width + right)
  }
  for (let next = 0; next < size; next += 1) {
    const index = queue[next]
    const x = index % width
    const y = Math.floor(index / width)
    for (let dy = Math.max(-1, top - y); dy <= Math.min(1, bottom - y); dy += 1) {
      for (let dx = Math.max(-1, left - x); dx <= Math.min(1, right - x); dx += 1) mark(index + dy * width + dx)
    }
  }
  for (let y = top; y <= bottom; y += 1) {
    for (let i = y * width + left; i <= y * width + right; i += 1) mask[i] = Number(mask[i] !== 2)
  }
}

function boundaryStrength(channels: Float32Array[], width: number, height: number) {
  const gradient = new Float32Array(width * height)
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const index = y * width + x
      let squared = 0
      for (const channel of channels) {
        squared += (channel[index + 1] - channel[index - 1]) ** 2
          + (channel[index + width] - channel[index - width]) ** 2
      }
      gradient[index] = Math.sqrt(squared) * .5
    }
  }
  return gradient
}

function scoreRegion(
  mask: Uint8Array, width: number, bounds: Bounds, valid: Uint8Array,
  gradient: Float32Array, texture: Float32Array, barriers?: EdgeBarriers,
) {
  let size = 0
  let perimeter = 0
  let edgePixels = 0
  let edgeSum = 0
  let interiorSum = 0
  let textureSum = 0
  let interiorBarriers = 0
  for (let y = bounds.top; y <= bounds.bottom; y += 1) {
    for (let i = y * width + bounds.left; i <= y * width + bounds.right; i += 1) {
      if (!mask[i]) continue
      if (!valid[i]) return null
      size += 1
      interiorSum += gradient[i]
      textureSum += texture[i]
      if (barriers?.horizontal[i] && mask[i + 1] && mask[i - 2] && mask[i + 3]) interiorBarriers += 1
      if (barriers?.vertical[i] && mask[i + width] && mask[i - 2 * width] && mask[i + 3 * width]) interiorBarriers += 1
      const edges = 4 - mask[i - 1] - mask[i + 1] - mask[i - width] - mask[i + width]
      if (edges) {
        perimeter += edges
        edgePixels += 1
        edgeSum += gradient[i]
      }
    }
  }
  if (size < Math.max(48, .0005 * mask.length) || size > .66 * mask.length || !edgePixels) return null

  const boundary = edgeSum / edgePixels
  const inside = interiorSum / size
  const contrast = (boundary + 1.5) / (inside + 1.5)
  // A coherent field should show a detectable edge, rather than merely being a patch of
  // color similar to the click. Penalize fragmented borders and seed-sized patches.
  if (boundary < 2.3 || contrast < 1.35) return null
  const roughness = Math.max(1, perimeter / (4 * Math.sqrt(size)))
  const extent = Math.min(1, Math.sqrt(size / Math.max(200, .008 * mask.length)))
  const spread = 1 + 3 * size / mask.length
  const texturePenalty = Math.max(1, textureSum / size / 12)
  const barrierPenalty = 1 + 3 * interiorBarriers / perimeter
  return extent * Math.min(4, contrast) * Math.min(1, boundary / 5)
    / (roughness ** .85 * spread * texturePenalty * barrierPenalty)
}

function traceOutline(mask: Uint8Array, width: number, height: number): PixelPoint[] {
  // Marching squares uses edge midpoints on a doubled integer lattice. Separating
  // diagonal foreground corners avoids repeated vertices in the traced outline.
  const stride = 2 * width + 1
  const graph = new Map<number, number[]>()
  function connect(a: number, b: number) {
    const aNeighbors = graph.get(a)
    const bNeighbors = graph.get(b)
    if (aNeighbors) aNeighbors.push(b)
    else graph.set(a, [b])
    if (bNeighbors) bNeighbors.push(a)
    else graph.set(b, [a])
  }
  for (let y = 0; y < height - 1; y += 1) {
    for (let x = 0; x < width - 1; x += 1) {
      const index = y * width + x
      const a = mask[index]
      const b = mask[index + 1]
      const c = mask[index + width + 1]
      const d = mask[index + width]
      if (a === b && a === c && a === d) continue
      const center = (2 * y + 2) * stride + 2 * x + 2
      const top = center - stride
      const right = center + 1
      const bottom = center + stride
      const left = center - 1
      if (a === c && b === d) {
        if (a) {
          connect(top, left)
          connect(right, bottom)
        } else {
          connect(top, right)
          connect(bottom, left)
        }
      } else {
        const edges: number[] = []
        if (a !== b) edges.push(top)
        if (b !== c) edges.push(right)
        if (c !== d) edges.push(bottom)
        if (d !== a) edges.push(left)
        connect(edges[0], edges[1])
      }
    }
  }
  const start = graph.keys().next().value
  if (start === undefined) return []
  const ring: PixelPoint[] = []
  let current = start
  let previous = -1
  do {
    const neighbors = graph.get(current)
    if (neighbors?.length !== 2 || ring.length >= graph.size) return []
    ring.push([(current % stride) * .5, Math.floor(current / stride) * .5])
    const next = neighbors[0] === previous ? neighbors[1] : neighbors[0]
    previous = current
    current = next
  } while (current !== start)
  return ring
}

function distanceToSegment(point: PixelPoint, a: PixelPoint, b: PixelPoint) {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const lengthSquared = dx * dx + dy * dy
  const t = lengthSquared ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSquared)) : 0
  return (point[0] - a[0] - t * dx) ** 2 + (point[1] - a[1] - t * dy) ** 2
}

function simplifyRing(ring: PixelPoint[], tolerance: number) {
  let opposite = 0
  let longest = 0
  for (let i = 1; i < ring.length; i += 1) {
    const length = (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2
    if (length > longest) {
      longest = length
      opposite = i
    }
  }
  const kept = new Uint8Array(ring.length)
  kept[0] = 1
  kept[opposite] = 1
  const ranges = [[0, opposite], [opposite, ring.length]]
  while (ranges.length) {
    const [start, end] = ranges.pop()!
    let furthest = -1
    let distance = tolerance ** 2
    for (let i = start + 1; i < end; i += 1) {
      const value = distanceToSegment(ring[i], ring[start], ring[end % ring.length])
      if (value > distance) {
        distance = value
        furthest = i
      }
    }
    if (furthest >= 0) {
      kept[furthest] = 1
      ranges.push([start, furthest], [furthest, end])
    }
  }
  return ring.filter((_, i) => kept[i])
}

function coversSeed(ring: PixelPoint[], [x, y]: PixelPoint) {
  let inside = false
  for (let i = 0; i < ring.length; i += 1) {
    const [ax, ay] = ring[i]
    const [bx, by] = ring[(i + 1) % ring.length]
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) inside = !inside
  }
  return inside
}

function side(a: PixelPoint, b: PixelPoint, c: PixelPoint) {
  return Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
}

function onSegment(a: PixelPoint, b: PixelPoint, point: PixelPoint) {
  return point[0] >= Math.min(a[0], b[0]) && point[0] <= Math.max(a[0], b[0])
    && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1])
}

function simpleRing(ring: PixelPoint[]) {
  for (let i = 0; i < ring.length; i += 1) {
    const a = ring[i]
    const b = ring[(i + 1) % ring.length]
    for (let j = i + 2; j < ring.length; j += 1) {
      if (i === 0 && j === ring.length - 1) continue
      const c = ring[j]
      const d = ring[(j + 1) % ring.length]
      const abc = side(a, b, c)
      const abd = side(a, b, d)
      const cda = side(c, d, a)
      const cdb = side(c, d, b)
      if ((abc * abd < 0 && cda * cdb < 0) || (abc === 0 && onSegment(a, b, c))
          || (abd === 0 && onSegment(a, b, d)) || (cda === 0 && onSegment(c, d, a))
          || (cdb === 0 && onSegment(c, d, b))) return false
    }
  }
  return true
}

/** Suggest an editable, open outline in pixel-edge coordinates from one click. */
export function segmentField({ width, height, pixels, seed, source, spectralFrames }: FieldSegmentationInput): PixelPoint[] | null {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < minimumSize || height < minimumSize
      || width > maximumSize || height > maximumSize || pixels.length !== 4 * width * height) return null
  const x = Math.floor(seed[0])
  const y = Math.floor(seed[1])
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 5 || x >= width - 5 || y < 5 || y >= height - 5) return null
  const size = width * height
  const seedIndex = y * width + x
  const valid = Uint8Array.from({ length: size }, (_, i) => Number(pixels[i * 4 + 3] >= 240))
  for (let row = y - 4; row <= y + 4; row += 1) {
    for (let col = x - 4; col <= x + 4; col += 1) if (!valid[row * width + col]) return null
  }
  const satellite = source === 'satellite'
  const radius = satellite || Math.min(width, height) < 180 ? 1 : 2
  const channels = imageFeatures(width, height, pixels, radius)
  const center = seedFeatures(channels, width, height, x, y)
  const spectral = satellite && spectralFrames?.length ? prepareSpectralFrames(width, height, spectralFrames, x, y) : []
  const rgbWeight = spectral.length > 1 ? .55 : .8
  const spectralWeight = spectral.length > 1 ? 1.15 : .65
  // A highly textured click seldom supplies a coherent seed for one crop stand.
  if (center[3] > 25) return null
  const distances = Float32Array.from({ length: size }, (_, index) => {
    if (!valid[index]) return Infinity
    let squared = 0
    for (let i = 0; i < channels.length; i += 1) squared += (channels[i][index] - center[i]) ** 2
    let spectralSquared = 0
    let dates = 0
    for (const { channels: [nir, ndvi], center: [seedNir, seedNdvi] } of spectral) {
      if (!Number.isFinite(nir[index])) continue
      spectralSquared += (nir[index] - seedNir) ** 2 + (ndvi[index] - seedNdvi) ** 2
      dates += 1
    }
    if (dates) return rgbWeight * squared + spectralWeight * spectralSquared / dates
    return squared
  })
  const gradient = boundaryStrength(channels, width, height)
  const barriers = satellite ? coherentBarriers(width, height, pixels, valid, x, y) : undefined
  if (barriers && spectral.length) {
    addSpectralGradient(gradient, spectral, width, height)
    addSpectralBarriers(barriers, spectral, width, height)
  }
  const queue = new Int32Array(size)
  const dataMargin = morphology(valid, width, height, 4, false)
  let bestScore = satellite ? .90 : 1.05
  let best: { mask: Uint8Array; bounds: Bounds } | undefined
  let lastSize = 0
  const allowed = new Uint8Array(size)
  for (const tolerance of satellite ? satelliteTolerances : tolerances) {
    const limit = tolerance ** 2
    for (let i = 0; i < size; i += 1) allowed[i] = Number(distances[i] <= limit)
    const raw = component(allowed, seedIndex, width, height, queue, barriers)
    if (raw.size < 48 || raw.size === lastSize || raw.size > .66 * size) continue
    lastSize = raw.size
    if (raw.bounds.left <= 1 || raw.bounds.top <= 1 || raw.bounds.right >= width - 2 || raw.bounds.bottom >= height - 2) break
    const expanded = morphology(raw.selected, width, height, radius, true)
    const tightened = morphology(expanded, width, height, 2 * radius, false)
    const cleaned = morphology(tightened, width, height, radius, true)
    const candidate = component(cleaned, seedIndex, width, height, queue, barriers)
    if (candidate.size < 48) continue
    if (candidate.bounds.left <= 1 || candidate.bounds.top <= 1 || candidate.bounds.right >= width - 2 || candidate.bounds.bottom >= height - 2) continue
    fillHoles(candidate.selected, width, candidate.bounds, queue)
    const score = scoreRegion(candidate.selected, width, candidate.bounds, dataMargin, gradient, channels[3], barriers)
    if (score !== null && score > bestScore) {
      bestScore = score
      best = { mask: candidate.selected, bounds: candidate.bounds }
    }
  }
  if (!best) return null
  const outline = traceOutline(best.mask, width, height)
  if (outline.length < 3) return null
  const { left, right, top, bottom } = best.bounds
  const preferredTolerance = Math.min(3, Math.max(1.25, Math.hypot(right - left, bottom - top) / 110))
  for (const tolerance of [preferredTolerance, .8, 2, 3, 4, 6]) {
    const ring = simplifyRing(outline, tolerance)
    if (ring.length >= 3 && ring.length <= maxVertices && coversSeed(ring, seed) && simpleRing(ring)) return ring
  }
  return null
}
