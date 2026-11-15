import type { ReactNode } from 'react'
import type { DashboardSource, DashboardSpan } from '../../api/fieldWeather'
import { useLocale } from '../../shared/locale'
import { periodDays, type Period } from './dashboardModel'

export function useDashboardLabels() {
  const locale = useLocale()
  const label = (en: string, uk: string) => locale.language === 'uk' ? uk : en
  const dates = ({ start, end }: Period) => start === end ? locale.date(start) : `${locale.date(start)} – ${locale.date(end)}`
  const source = (kind: DashboardSource) => ({
    reanalysis: label('Historical reanalysis', 'Історичний реаналіз'),
    forecast: label('Operational forecast', 'Оперативний прогноз'),
    climate: label('Climate scenario', 'Кліматичний сценарій'),
  })[kind]
  return { ...locale, label, dates, source }
}

export function DashboardChart({ title, unit, period, spans, describe, details, caption, threshold, digits = 1 }: {
  title: string; unit: string; period: Period; spans: DashboardSpan[]
  describe?: (span: DashboardSpan) => string; details?: ReactNode; caption?: string; threshold?: number; digits?: number
}) {
  const { label, dates, shortDate, number, source } = useDashboardLabels()
  const data = spans.flatMap(span => span.source ? span.points : [])
  const height = 122
  const width = 440
  const floor = Math.min(0, ...data.map(point => point.value))
  const ceiling = Math.max(10 ** -digits, threshold ?? 0, ...data.map(point => point.value))
  const y = (value: number) => 14 + height - (value - floor) * height / (ceiling - floor)
  const day = width / periodDays(period)
  const x = (date: string) => 52 + (periodDays({ start: period.start, end: date }) - 1) * day
  const sourceName = (span: DashboardSpan) => describe?.(span) ?? (span.source ? source(span.source) : '')
  return <figure className="dashboard-chart">
    <figcaption><h3>{title} <small>· {unit}</small></h3><p>{dates(period)} · UTC{caption && ` · ${caption}`}</p></figcaption>
    {data.length > 0 && <svg viewBox="0 0 510 164" role="img"
      aria-label={`${title} · ${unit} · ${dates(period)}`}>
      {[floor, (floor + ceiling) / 2, ceiling].map(value => <g key={value}>
        <line className="dashboard-gridline" x1="52" x2="492" y1={y(value)} y2={y(value)} />
        <text x="45" y={y(value) + 4} textAnchor="end">{number(value, digits)}</text>
      </g>)}
      {spans.flatMap(span => !span.source ? [] : span.points.map(point => {
        const bar = Math.max(1, day * periodDays(point) - 2)
        return <rect key={point.start} className={`dashboard-bar is-${span.source}`} x={x(point.start) + 1}
          width={bar} y={Math.min(y(0), y(point.value))} height={Math.max(1, Math.abs(y(0) - y(point.value)))}>
          <title>{`${dates(point)} · ${number(point.value, digits)} ${unit} · ${sourceName(span)}`}</title>
        </rect>
      }))}
      {threshold !== undefined && <line className="dashboard-threshold" x1="52" x2="492" y1={y(threshold)} y2={y(threshold)} />}
      <text x="52" y="157">{shortDate(period.start)}</text>
      {period.start !== period.end && <text x="492" y="157" textAnchor="end">{shortDate(period.end)}</text>}
    </svg>}
    <ul className="dashboard-sources" aria-label={label('Data by date', 'Дані за датами')}>
      {spans.map(span => <li key={span.start} className={`is-${span.source ?? 'gap'}`}>
        <span className="dashboard-marker" aria-hidden="true" />
        <span>{sourceName(span)} <small>· {dates(span)}</small></span>
      </li>)}
    </ul>
    {(data.length > 0 || details) && <details className="dashboard-details">
      <summary>{label('Values and sources', 'Значення та джерела')}</summary>
      {details}
      {data.length > 0 && <table>
        <caption>{title} · {unit}</caption>
        <thead><tr><th scope="col">{label('Period', 'Період')}</th><th scope="col">{unit}</th><th scope="col">{label('Source', 'Джерело')}</th></tr></thead>
        <tbody>{spans.flatMap(span => span.points.map(point => <tr key={point.start}>
          <td>{dates(point)}</td><td>{number(point.value, digits)}</td><td>{sourceName(span)}</td>
        </tr>))}</tbody>
      </table>}
    </details>}
  </figure>
}
