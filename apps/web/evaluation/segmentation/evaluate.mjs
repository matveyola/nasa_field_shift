import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

const iouTarget = 0.7
const highLeakThreshold = 0.2
const mergeThreshold = 0.1
const boundaryTolerance = 2
const boundaryOffsets = []
for (let y = -boundaryTolerance; y <= boundaryTolerance; y += 1) {
  for (let x = -boundaryTolerance; x <= boundaryTolerance; x += 1) {
    if (x * x + y * y <= boundaryTolerance ** 2) boundaryOffsets.push([x, y])
  }
}
const splits = new Set(['tuning', 'holdout', 'regression'])
const defaultEngine = new URL('../../src/features/fields/segmentationEngine.ts', import.meta.url)

function pointIsFinite(point) {
  return Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
}

function isRing(ring) {
  return Array.isArray(ring) && ring.length >= 3 && ring.every(pointIsFinite)
}

function rowIntersections(ring, y) {
  const intersections = []
  for (let index = 0; index < ring.length; index += 1) {
    const [ax, ay] = ring[index]
    const [bx, by] = ring[(index + 1) % ring.length]
    if ((ay > y) !== (by > y)) intersections.push(ax + (y - ay) * (bx - ax) / (by - ay))
  }
  return intersections.sort((a, b) => a - b)
}

// Evaluate at pixel centers in the same edge-coordinate system as the engine.
// Scanlines clip polygons to the window without altering their reference vertices.
export function rasterizeRing(ring, width, height) {
  const pixels = new Uint8Array(width * height)
  let area = 0
  for (let row = 0; row < height; row += 1) {
    const intersections = rowIntersections(ring, row + 0.5)
    for (let index = 0; index + 1 < intersections.length; index += 2) {
      const first = Math.max(0, Math.ceil(intersections[index] - 0.5))
      const last = Math.min(width, Math.ceil(intersections[index + 1] - 0.5))
      if (last <= first) continue
      pixels.fill(1, row * width + first, row * width + last)
      area += last - first
    }
  }
  return { pixels, area }
}

function containsPoint(ring, [x, y]) {
  const intersections = rowIntersections(ring, y)
  for (let index = 0; index + 1 < intersections.length; index += 2) {
    if (x >= intersections[index] && x < intersections[index + 1]) return true
  }
  return false
}

function getBoundary({ pixels }, width, height) {
  const indices = []
  const mask = new Uint8Array(pixels.length)
  for (let index = 0; index < pixels.length; index += 1) {
    if (!pixels[index]) continue
    const column = index % width
    const row = Math.floor(index / width)
    if (column === 0 || column === width - 1 || row === 0 || row === height - 1
      || !pixels[index - 1] || !pixels[index + 1] || !pixels[index - width] || !pixels[index + width]) {
      indices.push(index)
      mask[index] = 1
    }
  }
  return { indices, mask }
}

function boundaryMatchRate(from, to, width, height) {
  let matches = 0
  for (const index of from.indices) {
    const x = index % width
    const y = Math.floor(index / width)
    matches += Number(boundaryOffsets.some(([dx, dy]) =>
      x + dx >= 0 && x + dx < width && y + dy >= 0 && y + dy < height
      && to.mask[(y + dy) * width + x + dx]))
  }
  return from.indices.length ? matches / from.indices.length : 0
}

export function scoreProposal(ring, reference, neighbors, width, height, seed) {
  const candidate = isRing(ring) && ring.length <= 127 && containsPoint(ring, seed)
    && ring.every(([x, y]) => x >= 0 && x <= width && y >= 0 && y <= height)
  const proposal = candidate ? rasterizeRing(ring, width, height) : null
  if (!proposal?.area) {
    return {
      status: ring === null ? 'rejected' : 'invalid',
      vertices: 0,
      iou: 0,
      recall: 0,
      boundaryF1: 0,
      leak: null,
      highLeak: false,
      merged: false,
      neighboringOverlap: [],
    }
  }

  let intersection = 0
  for (let index = 0; index < proposal.pixels.length; index += 1) {
    intersection += proposal.pixels[index] * reference.pixels[index]
  }
  const proposalBoundary = getBoundary(proposal, width, height)
  const referenceBoundary = getBoundary(reference, width, height)
  const boundaryPrecision = boundaryMatchRate(proposalBoundary, referenceBoundary, width, height)
  const boundaryRecall = boundaryMatchRate(referenceBoundary, proposalBoundary, width, height)
  const boundaryTotal = boundaryPrecision + boundaryRecall
  const leak = (proposal.area - intersection) / proposal.area
  const nearTarget = Uint8Array.from(reference.pixels)
  for (const index of referenceBoundary.indices) {
    const x = index % width
    const y = Math.floor(index / width)
    for (const [dx, dy] of boundaryOffsets) {
      if (x + dx >= 0 && x + dx < width && y + dy >= 0 && y + dy < height) {
        nearTarget[(y + dy) * width + x + dx] = 1
      }
    }
  }
  const neighboringOverlap = neighbors.map(({ id, pixels }) => {
    let neighboringPixels = 0
    let predictedNeighboringPixels = 0
    let pastBoundaryTolerance = 0
    for (let index = 0; index < pixels.length; index += 1) {
      if (pixels[index] && !reference.pixels[index]) {
        neighboringPixels += 1
        predictedNeighboringPixels += proposal.pixels[index]
        if (!nearTarget[index]) pastBoundaryTolerance += proposal.pixels[index]
      }
    }
    return {
      id,
      pixels: predictedNeighboringPixels,
      shareOfProposal: predictedNeighboringPixels / proposal.area,
      shareOfNeighbor: neighboringPixels ? predictedNeighboringPixels / neighboringPixels : 0,
      pastBoundaryTolerance,
      merge: pastBoundaryTolerance / proposal.area >= mergeThreshold
        && neighboringPixels > 0 && pastBoundaryTolerance / neighboringPixels >= mergeThreshold,
    }
  })
  return {
    status: 'accepted',
    vertices: ring.length,
    iou: intersection / (proposal.area + reference.area - intersection),
    recall: intersection / reference.area,
    boundaryF1: boundaryTotal ? 2 * boundaryPrecision * boundaryRecall / boundaryTotal : 0,
    leak,
    highLeak: leak >= highLeakThreshold,
    merged: neighboringOverlap.some(({ merge }) => merge),
    neighboringOverlap,
  }
}

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
}

function percentile(values, fraction) {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)]
}

export function summarize(results) {
  const accepted = results.filter(({ status }) => status === 'accepted')
  const iouAtLeast70 = results.filter(({ iou }) => iou >= iouTarget).length
  const neighborsAnnotated = results.filter(({ neighborCount }) => neighborCount > 0).length
  return {
    seeds: results.length,
    accepted: accepted.length,
    coverage: results.length ? accepted.length / results.length : null,
    rejected: results.filter(({ status }) => status === 'rejected').length,
    invalid: results.filter(({ status }) => status === 'invalid').length,
    iouAtLeast70,
    meanIou: mean(results.map(({ iou }) => iou)),
    meanAcceptedIou: mean(accepted.map(({ iou }) => iou)),
    meanBoundaryF1: mean(results.map(({ boundaryF1 }) => boundaryF1)),
    meanAcceptedLeak: mean(accepted.map(({ leak }) => leak)),
    highLeak: accepted.filter(({ highLeak }) => highLeak).length,
    merged: accepted.filter(({ merged }) => merged).length,
    neighborsAnnotated,
    medianMs: percentile(results.map(({ durationMs }) => durationMs), 0.5),
    p95Ms: percentile(results.map(({ durationMs }) => durationMs), 0.95),
  }
}

function groupsBy(results, property) {
  const groups = new Map()
  for (const result of results) {
    const name = result[property]
    const group = groups.get(name) ?? []
    group.push(result)
    groups.set(name, group)
  }
  return Object.fromEntries([...groups].map(([name, group]) => [name, summarize(group)]))
}

function validateManifest(cases) {
  if (!Array.isArray(cases) || !cases.length) throw new Error('Manifest must contain at least one case.')
  const ids = new Set()
  const siteSplits = new Map()
  for (const entry of cases) {
    const { id, site, split, source, image, reference, neighbors = [], seeds } = entry
    if (!id || !site || !splits.has(split) || !['satellite', 'aerial'].includes(source)) {
      throw new Error('Every case needs an id, site, split and imagery source.')
    }
    if (ids.has(id)) throw new Error('Duplicate case id: ' + id)
    ids.add(id)
    if (siteSplits.has(site) && siteSplits.get(site) !== split) {
      throw new Error('Site cannot appear in both development and validation splits: ' + site)
    }
    siteSplits.set(site, split)
    if (!image?.path || ![image.width, image.height].every((value) => Number.isInteger(value) && value >= 24 && value <= 640)) {
      throw new Error('Expected raw RGBA image path and valid dimensions for ' + id)
    }
    if (!isRing(reference) || !neighbors.every((neighbor) => neighbor.id && isRing(neighbor.reference))) {
      throw new Error('Expected finite pixel-coordinate rings for ' + id)
    }
    if (!Array.isArray(seeds) || !seeds.length) throw new Error('Missing seeds for ' + id)
    const seedIds = new Set()
    for (const [index, seed] of seeds.entries()) {
      const point = Array.isArray(seed) ? seed : seed.point
      const seedId = Array.isArray(seed) ? String(index + 1) : seed.id
      if (!seedId || seedIds.has(seedId) || !pointIsFinite(point)
        || point[0] < 0 || point[0] >= image.width || point[1] < 0 || point[1] >= image.height
        || !containsPoint(reference, point)) throw new Error('Invalid or out-of-field seed for ' + id)
      seedIds.add(seedId)
    }
  }
}

async function readEngines(specs) {
  if (!specs?.length) return [{ name: 'current', module: defaultEngine, ...(await import(defaultEngine)) }]
  const engines = []
  const names = new Set()
  for (const spec of specs) {
    const divider = spec.indexOf('=')
    const name = divider < 0 ? spec : spec.slice(0, divider)
    const path = divider < 0 ? spec : spec.slice(divider + 1)
    if (!name || !path || names.has(name)) throw new Error('Each --engine needs a unique name and path.')
    names.add(name)
    const module = pathToFileURL(resolve(path))
    const { segmentField } = await import(module)
    if (typeof segmentField !== 'function') throw new Error('Engine does not export segmentField: ' + path)
    engines.push({ name, module, segmentField })
  }
  return engines
}

function selectFrameCounts(specs, engines) {
  const counts = new Map()
  for (const spec of specs ?? []) {
    const [name, count] = spec.split('=')
    if (!engines.some((engine) => engine.name === name) || counts.has(name) || !/^[1-4]$/.test(count)) {
      throw new Error('Each --frames option needs an engine name and a maximum of 1 to 4 dates.')
    }
    counts.set(name, Number(count))
  }
  return counts
}

async function readSpectralFrames(entry, directory, count) {
  if (!count) return []
  const frames = entry.spectralFrames?.slice(0, count) ?? []
  if (!frames.length || new Set(frames.map(({ scene }) => scene)).size !== frames.length) {
    throw new Error('Expected distinct spectral acquisitions for ' + entry.id)
  }
  const length = entry.image.width * entry.image.height
  return Promise.all(frames.map(async ({ path, scene }) => {
    if (!path || !scene) throw new Error('Missing spectral frame path or scene for ' + entry.id)
    const bytes = await readFile(resolve(directory, path))
    if (bytes.length !== length * 8) throw new Error('Unexpected spectral byte count for ' + entry.id)
    const data = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    const nir = new Float32Array(length)
    const ndvi = new Float32Array(length)
    for (let i = 0; i < length; i += 1) {
      nir[i] = data.getFloat32(i * 4, true)
      ndvi[i] = data.getFloat32((length + i) * 4, true)
    }
    return { scene, nir, ndvi }
  }))
}

async function evaluate(manifestPath, chosenSplit, engines, frameCounts) {
  const { cases } = JSON.parse(await readFile(manifestPath, 'utf8'))
  validateManifest(cases)
  const results = engines.map(({ name, module }) => ({ name, module: module.href, rows: [] }))
  const maxFrames = Math.max(0, ...frameCounts.values())
  for (const entry of cases) {
    const { id, site, split, source, image, seeds, field, scene } = entry
    if (chosenSplit && split !== chosenSplit) continue
    const { width, height } = image
    const bytes = await readFile(resolve(dirname(manifestPath), image.path))
    if (bytes.length !== width * height * 4) throw new Error('Unexpected RGBA byte count for ' + id)
    const pixels = new Uint8ClampedArray(bytes)
    const spectralFrames = await readSpectralFrames(entry, dirname(manifestPath), maxFrames)
    const reference = rasterizeRing(entry.reference, width, height)
    if (!reference.area) throw new Error('Empty target mask for ' + id)
    const referenceClipped = entry.reference.some(([x, y]) => x < 0 || x > width || y < 0 || y > height)
    const neighbors = (entry.neighbors ?? []).map(({ id, reference }) =>
      ({ id, ...rasterizeRing(reference, width, height) })).filter(({ area }) => area > 0)
    for (const [index, seed] of seeds.entries()) {
      const point = Array.isArray(seed) ? seed : seed.point
      const seedId = Array.isArray(seed) ? String(index + 1) : seed.id
      for (const [engineIndex, engine] of engines.entries()) {
        const frames = spectralFrames.slice(0, frameCounts.get(engine.name) ?? 0)
        const input = { width, height, pixels, seed: point, source, spectralFrames: frames }
        const start = performance.now()
        const ring = engine.segmentField(input)
        const durationMs = performance.now() - start
        results[engineIndex].rows.push({
          case: id, site, split, field, scene, source, seedId, seed: point, referenceClipped,
          spectralScenes: frames.map(({ scene }) => scene),
          neighborCount: neighbors.length, durationMs, outline: ring,
          ...scoreProposal(ring, reference, neighbors, width, height, point),
        })
      }
    }
  }
  if (!results[0].rows.length) throw new Error('No evaluation cases matched the selected split.')
  return results.map(({ name, module, rows }) => ({
    name, module, summary: summarize(rows), bySplit: groupsBy(rows, 'split'), bySite: groupsBy(rows, 'site'), rows,
  }))
}

async function main() {
  const { values, positionals } = parseArgs({
    options: {
      engine: { type: 'string', multiple: true },
      frames: { type: 'string', multiple: true },
      split: { type: 'string' },
      output: { type: 'string' },
    },
    allowPositionals: true,
  })
  if (positionals.length !== 1 || (values.split && !splits.has(values.split))) {
    throw new Error('Usage: node evaluate.mjs manifest.json [--engine name=path.ts] [--frames name=count] [--split tuning|holdout|regression] [--output report.json]')
  }
  const manifest = resolve(positionals[0])
  const inputs = await readEngines(values.engine)
  const engines = await evaluate(manifest, values.split, inputs, selectFrameCounts(values.frames, inputs))
  const report = {
    manifest,
    thresholds: { iouTarget, highLeakThreshold, mergeThreshold, boundaryTolerancePixels: boundaryTolerance },
    engines,
  }
  if (!values.output) {
    console.log(JSON.stringify(report, null, 2))
  } else {
    await writeFile(values.output, JSON.stringify(report, null, 2) + '\n')
    console.table(engines.flatMap(({ name, bySplit }) =>
      Object.entries(bySplit).map(([split, summary]) => ({ engine: name, split, ...summary }))))
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  await main().catch((error) => {
    console.error(error.message)
    process.exitCode = 1
  })
}
