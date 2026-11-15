import { ArrowRightIcon, InformationCircleIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'
import { useId } from 'react'
import type { Field, WaterForecast } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { AssistantMark } from '../../shared/AssistantMark'
import { waterReferenceDisplay } from '../water/waterReferenceDisplay'
import type { getWaterOutlook } from '../water/waterOutlook'
import './fieldWaterAssistant.css'

type Props = {
  field: Field
  forecast: { data?: WaterForecast | null; pending: boolean; error?: string }
  outlook: ReturnType<typeof getWaterOutlook>
  openForecast: (fieldId: string, day?: string) => void
}

export function FieldWaterAssistant({ field, forecast, outlook, openForecast }: Props) {
  const headingId = useId()
  const { t, number, date, shortDate, language } = useLocale()
  const label = (en: string, uk: string) => language === 'uk' ? uk : en
  const shortage = outlook.kind === 'shortage'
  const available = outlook.kind !== 'unavailable'
  const applicability = available ? forecast.data?.applicability : null
  const display = waterReferenceDisplay(applicability, shortage, language)
  const digits = shortage && number(outlook.firstShortage.reserve_mm) === number(outlook.thresholdMm) ? 4 : 1
  let unavailableMessage = t('No forecast for the upcoming days yet.')
  if (forecast.pending) unavailableMessage = t('Loading root-zone forecast…')
  else if (forecast.error || (outlook.kind === 'unavailable' && outlook.reason === 'invalid')) {
    unavailableMessage = t('Could not load this field’s water forecast.')
  } else if (outlook.kind === 'unavailable' && ['expired', 'stale'].includes(outlook.reason)) {
    unavailableMessage = t('The last saved run has expired.')
  }

  return (
    <section className="field-water-assistant" aria-labelledby={headingId}>
      <header className="field-water-assistant-header">
        <AssistantMark size={34} />
        <h2 id={headingId}>{t('Field assistant')}</h2>
        <span className="field-water-assistant-badge">{t('Forecast signal')}</span>
      </header>
      <div className={`field-water-assistant-status is-${available ? display.tone : outlook.kind}`} role="status">
        {available ? <>
          {shortage && applicability?.status !== 'review_required' ? <ExclamationTriangleIcon aria-hidden="true" /> : <InformationCircleIcon aria-hidden="true" />}
          <div>
            <h3>{display.summary}</h3>
            <p>{t(shortage ? 'First day below threshold' : 'Forecast period')}{' '}
              <time dateTime={shortage ? outlook.firstShortage.date : outlook.firstDay.date}>
                {shortDate(shortage ? outlook.firstShortage.date : outlook.firstDay.date)}
              </time>
              {!shortage && outlook.lastDay.date !== outlook.firstDay.date && <>{' – '}<time dateTime={outlook.lastDay.date}>{shortDate(outlook.lastDay.date)}</time></>}
              {shortage && <> · {number(outlook.thresholdMm, digits)} {t('mm')}</>}
            </p>
          </div>
        </> : <p>{unavailableMessage}</p>}
      </div>
      {available && <p role="note">{display.caveat} {display.limitation}</p>}
      {shortage && <dl className="field-assistant-risk-values">
        <div><dt>{shortDate(outlook.firstShortage.date)}</dt><dd>{number(outlook.firstShortage.reserve_mm, digits)} {t('mm')}</dd></div>
        <div><dt>{label('Reference threshold', 'Довідковий поріг')}</dt><dd>{number(outlook.thresholdMm, digits)} {t('mm')}</dd></div>
      </dl>}
      {available && <WaterMiniChart days={outlook.days} threshold={outlook.thresholdMm} />}
      <button type="button" className="button primary field-water-assistant-action"
        onClick={() => openForecast(field.id, shortage ? outlook.firstShortage.date : undefined)}>
        {t('Open water forecast')} <ArrowRightIcon aria-hidden="true" />
      </button>
      {available && forecast.data && <p className="field-water-assistant-source">
        FAO-56 · {forecast.data.provider} · UTC<br />
        {t('Initialized')}{' '}<time dateTime={forecast.data.initialized_at}>{date(forecast.data.initialized_at)}</time>
      </p>}
    </section>
  )
}

function WaterMiniChart({ days, threshold }: { days: WaterForecast['days']; threshold: number }) {
  const { t, shortDate, number } = useLocale()
  const fillId = useId()
  const bottom = 132
  const low = Math.max(0, Math.floor(Math.min(threshold, ...days.map((day) => day.reserve_mm)) / 10) * 10)
  const high = Math.max(low + 20, Math.ceil(Math.max(threshold, ...days.map((day) => day.reserve_mm)) / 10) * 10)
  const x = (index: number) => days.length === 1 ? 155 : 35 + index / (days.length - 1) * 240
  const y = (value: number) => bottom - (value - low) / (high - low) * 110
  const points = days.map((day, index) => `${x(index)},${y(day.reserve_mm)}`).join(' ')
  return <figure className="field-water-mini-chart">
    <figcaption>{t('Root-zone reserve')}, {t('mm')}</figcaption>
    <svg viewBox="0 0 300 158" role="img" aria-label={t('Root-zone water reserve by day, with stress threshold')}>
      <defs><linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1"><stop style={{ stopColor: 'var(--series-1)' }} stopOpacity=".12" /><stop offset="1" style={{ stopColor: 'var(--series-1)' }} stopOpacity=".01" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map((index) => {
        const value = low + (high - low) * index / 4
        return <g key={index}><line x1="32" x2="280" y1={y(value)} y2={y(value)} className="forecast-gridline" /><text x="24" y={y(value) + 3} textAnchor="end">{number(value, 0)}</text></g>
      })}
      <polygon points={`${x(0)},${bottom} ${points} ${x(days.length - 1)},${bottom}`} fill={`url(#${fillId})`} />
      <line x1="32" x2="280" y1={y(threshold)} y2={y(threshold)} style={{ stroke: 'var(--danger)' }} strokeDasharray="5 4" />
      <polyline points={points} fill="none" style={{ stroke: 'var(--series-1)' }} strokeWidth="2" />
      {days.map((day, index) => <g key={day.date}>
        <circle cx={x(index)} cy={y(day.reserve_mm)} r="3.5" style={{ fill: 'var(--series-1)' }} stroke="#fff" strokeWidth="1.5" />
        <text x={x(index)} y="151" textAnchor="middle">{shortDate(day.date)}</text>
      </g>)}
    </svg>
  </figure>
}
