import type { Polygon, Position } from '../../../api/contracts'
import type { PrecisionFeature, PrecisionGeometry } from '../../../api/precision'
import { useLocale } from '../../../shared/locale'

const palette = ['#285640', '#b28b4e', '#95a97c', '#588b9e', '#6a677f']
export function PlanPreview({ boundary, features, values }: { boundary: Polygon; features: PrecisionFeature[]; values?: Map<string, number | null> }) {
  const { t, number } = useLocale()
  const ring = boundary.coordinates[0]
  const west = Math.min(...ring.map(([x]) => x)), east = Math.max(...ring.map(([x]) => x))
  const south = Math.min(...ring.map(([, y]) => y)), north = Math.max(...ring.map(([, y]) => y))
  const ratio = Math.max(.1, Math.cos((south + north) * Math.PI / 360))
  const scale = Math.min(296 / Math.max((east - west) * ratio, 1e-10), 200 / Math.max(north - south, 1e-10))
  const point = ([x, y]: Position) => [160 + (x - (west + east) / 2) * scale * ratio, 112 + ((south + north) / 2 - y) * scale]
  const path = (geometry: PrecisionGeometry) => (geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates)
    .map((polygon) => polygon.map((outline) => 'M' + outline.map((vertex) => point(vertex).join(',')).join('L') + 'Z').join(' ')).join(' ')
  const zones = Array.from(new Set(features.map((item) => item.properties.zone).filter((zone) => zone !== 'fallback')))
  const known = values ? [...values.values()].filter((value): value is number => value !== null) : []
  const min = Math.min(...known), max = Math.max(...known)
  const color = (item: PrecisionFeature) => {
    if (item.properties.zone === 'fallback') return '#d6d6d6'
    if (!values) return palette[zones.indexOf(item.properties.zone) % palette.length]
    const value = values.get(item.properties.plot_id ?? '')
    return value == null ? '#dedede' : `hsl(130 24% ${70 - 42 * (max > min ? (value - min) / (max - min) : .5)}%)`
  }
  return <figure className="precision-plan-preview"><svg viewBox="0 0 320 224" role="img" aria-label={t(values ? 'Trial harvest map' : 'Application map preview')}>
    <path d={path(boundary)} style={{ fill: 'var(--sunken)', stroke: 'var(--series-2)' }} />
    {features.map((item, i) => <path key={i} d={path(item.geometry)} fillRule="evenodd" fill={color(item)} stroke="#fff" strokeWidth=".4">
      <title>{item.properties.plot_id ?? t(item.properties.zone)} · {values ? (values.get(item.properties.plot_id ?? '') == null ? t('No data') : number(values.get(item.properties.plot_id ?? '')!)) : number(item.properties.rate, 2)}</title>
    </path>)}
  </svg><figcaption>{t(values ? 'Grey areas have no trial measurements.' : 'Grey areas use the entered fallback rate.')}</figcaption></figure>
}
