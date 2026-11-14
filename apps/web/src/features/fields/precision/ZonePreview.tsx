import type { Polygon, Position } from '../../../api/contracts'
import { useLocale } from '../../../shared/locale'
import { zoneColors, zoneLabels, type Zone, type ZoneCell } from './zones'

export function ZonePreview({ boundary, cells, points = [] }: {
  boundary: Polygon
  cells: ZoneCell[]
  points?: { zone: Zone; position: Position }[]
}) {
  const { t } = useLocale()
  const ring = boundary.coordinates[0]
  const west = Math.min(...ring.map(([x]) => x))
  const east = Math.max(...ring.map(([x]) => x))
  const south = Math.min(...ring.map(([, y]) => y))
  const north = Math.max(...ring.map(([, y]) => y))
  const ratio = Math.max(.1, Math.cos((south + north) * Math.PI / 360))
  const scale = Math.min(248 / Math.max((east - west) * ratio, 1e-10), 136 / Math.max(north - south, 1e-10))
  const point = ([x, y]: Position): Position => [
    136 + (x - (west + east) / 2) * scale * ratio,
    80 + ((south + north) / 2 - y) * scale,
  ]
  const outline = (geometry: Polygon) => geometry.coordinates[0].map((vertex) => point(vertex).join(',')).join(' ')
  return <svg viewBox="0 0 272 160" role="img" aria-label={t('Map of NDVI zones')} className="precision-map">
    <polygon points={outline(boundary)} style={{ fill: 'var(--sunken)', stroke: 'var(--series-2)' }} strokeWidth="1" />
    {cells.map((cell, i) => <polygon key={i} points={outline(cell.geometry)} fill={zoneColors[cell.zone]} />)}
    {points.map(({ zone, position }) => {
      const [x, y] = point(position)
      return <circle key={zone} cx={x} cy={y} r="4.5" fill="#fff" style={{ stroke: 'var(--ink)' }} strokeWidth="1.5">
        <title>{t(zoneLabels[zone])}</title>
      </circle>
    })}
  </svg>
}
