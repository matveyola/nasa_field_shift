import { useId } from 'react'
import type { VegetationObservation } from '../../api/analytics'
import { useLocale } from '../../shared/locale'
import { adjacentObservationGroups } from './vegetationInsights'

export function SeasonalNdviChart({ observations, season, selected, onSelect }: {
  observations: VegetationObservation[]; season: number; selected?: string; onSelect: (date: string) => void
}) {
  const { language, t, number, shortDate } = useLocale()
  const id = useId()
  const yearStart = Date.UTC(season, 0, 1)
  const yearLength = Date.UTC(season + 1, 0, 1) - yearStart
  const min = Math.min(0, Math.floor(Math.min(...observations.flatMap((point) => [point.p10_ndvi, point.mean_ndvi])) * 2) / 2)
  const x = (day: string) => 34 + (Date.parse(day) - yearStart) / yearLength * 270
  const y = (value: number) => 160 - (value - min) / (1 - min) * 132
  const groups = adjacentObservationGroups(observations)
  return <figure className="seasonal-ndvi-chart">
    <svg viewBox="0 0 320 191" role="img" aria-labelledby={id}>
      <title id={id}>{t('Seasonal NDVI with the observed within-field range')}</title>
      {[0, 1, 2, 3].map((tick) => {
        const value = min + (1 - min) * tick / 3
        return <g key={tick}><line x1="34" x2="306" y1={y(value)} y2={y(value)} className="forecast-gridline" />
          <text x="26" y={y(value) + 3} textAnchor="end">{number(value, 1)}</text></g>
      })}
      {groups.map((group) => <g key={group[0].date}>
        {group.length > 1 && <polygon points={[
          ...group.map((point) => `${x(point.date)},${y(point.p90_ndvi)}`),
          ...[...group].reverse().map((point) => `${x(point.date)},${y(point.p10_ndvi)}`),
        ].join(' ')} style={{ fill: 'var(--series-3)' }} opacity=".45" />}
        <polyline points={group.map((point) => `${x(point.date)},${y(point.mean_ndvi)}`).join(' ')} fill="none" style={{ stroke: 'var(--series-1)' }} strokeWidth="2.3" />
      </g>)}
      {observations.map((point) => <g key={point.date}>
        <line x1={x(point.date)} x2={x(point.date)} y1={y(point.p10_ndvi)} y2={y(point.p90_ndvi)} style={{ stroke: 'var(--series-2)' }} strokeWidth="2" />
        {point.date === selected && <line x1={x(point.date)} x2={x(point.date)} y1="21" y2="160" style={{ stroke: 'var(--ink-3)' }} strokeDasharray="3 4" />}
        <circle cx={x(point.date)} cy={y(point.mean_ndvi)} r={point.date === selected ? 4.6 : 3.4} style={point.date === selected ? { fill: '#fff', stroke: 'var(--series-1)' } : { fill: 'var(--series-1)', stroke: '#fff' }} strokeWidth={point.date === selected ? 2.2 : 1.5} />
        <circle className="ndvi-point-target" cx={x(point.date)} cy={y(point.mean_ndvi)} r="9" fill="transparent"
          onClick={() => onSelect(point.date)} />
      </g>)}
      {[0, 2, 4, 6, 8, 10].map((month) => <text key={month} x={x(`${season}-${String(month + 1).padStart(2, '0')}-15`)} y="182" textAnchor="middle">
        {new Intl.DateTimeFormat(language === 'uk' ? 'uk-UA' : 'en', { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(season, month, 1)))}
      </text>)}
    </svg>
    <figcaption><span><i />{t('Mean NDVI')}</span><span><i className="ndvi-range" />{t('Within-field range · P10–P90')}</span></figcaption>
    <div className="ndvi-observation-dates" role="group" aria-label={t('Select satellite observation')}>
      {observations.map((point) => <button key={point.date} type="button" aria-pressed={point.date === selected}
        onClick={() => onSelect(point.date)}>{shortDate(point.date)}</button>)}
    </div>
  </figure>
}
