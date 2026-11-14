import type { VegetationObservation, VegetationResult } from '../../api/analytics'

export const ndviBands = [
  { min: -1, color: '#936448' },
  { min: 0, color: '#c19548' },
  { min: .25, color: '#c4bc72' },
  { min: .5, color: '#86a06b' },
  { min: .75, color: '#2b5940' },
] as const

export function ndviColor(value: number) {
  for (let index = ndviBands.length - 1; index > 0; index--) {
    if (value >= ndviBands[index].min) return ndviBands[index].color
  }
  return ndviBands[0].color
}

export function vegetationCsv(observations: VegetationObservation[]) {
  const heading = 'date,scene_id,mean_ndvi,p10_ndvi,p90_ndvi,mean_msavi,valid_percent,pixel_count,source_url'
  const escapeText = (text: string) => '"' + (/^[\s]*[=+@-]|^[\t\r\n]/.test(text) ? "'" : '') + text.replaceAll('"', '""') + '"'
  return [heading, ...observations.map((point) => [escapeText(point.date), escapeText(point.scene_id),
    point.mean_ndvi, point.p10_ndvi, point.p90_ndvi, point.mean_msavi ?? '', point.valid_percent, point.pixel_count, escapeText(point.catalog_url)].join(','))].join('\r\n') + '\r\n'
}

export function usableObservations(result: VegetationResult | undefined): VegetationObservation[] {
  return (result?.observations ?? []).filter((point) => /^\d{4}-\d{2}-\d{2}$/.test(point.date)
    && Number.isFinite(Date.parse(point.date)) && Number(point.date.slice(0, 4)) === result?.season
    && [point.mean_ndvi, point.p10_ndvi, point.p90_ndvi].every((value) => Number.isFinite(value) && value >= -1 && value <= 1)
    && point.p10_ndvi <= point.p90_ndvi && Number.isFinite(point.valid_percent) && point.valid_percent >= 70
    && point.valid_percent <= 100 && point.pixel_count >= 10)
    .sort((a, b) => a.date.localeCompare(b.date))
}

// A spatial contrast is an inspection cue, not a diagnosis or a crop-independent health score.
export function vegetationInsight(result: VegetationResult | undefined, today: string) {
  const layer = result?.heterogeneity
  const latest = usableObservations(result).at(-1)
  if (!layer || !latest || layer.date !== latest.date || !Number.isFinite(layer.low_area_percent)
    || layer.low_area_percent < 0 || layer.low_area_percent > 100) return undefined
  const age = (Date.parse(today) - Date.parse(layer.date)) / 86_400_000
  if (!Number.isFinite(age) || age < 0) return undefined
  return {
    date: layer.date,
    lowAreaPercent: layer.low_area_percent,
    validPercent: latest.valid_percent,
    stale: age > 30,
    // 10% of observed area below the explicit within-field threshold prompts an inspection.
    inspect: age <= 30 && layer.low_area_percent >= 10,
  }
}

export function adjacentObservationGroups(points: VegetationObservation[]) {
  const groups: VegetationObservation[][] = []
  for (const point of points) {
    const previous = groups.at(-1)?.at(-1)
    const month = Number(point.date.slice(5, 7))
    if (!previous || Number(previous.date.slice(5, 7)) + 1 !== month) groups.push([point])
    else groups.at(-1)!.push(point)
  }
  return groups
}
