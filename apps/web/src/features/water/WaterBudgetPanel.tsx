import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { WaterDay } from '../../api/contracts'
import { axisRange } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'
import { cumulativeWaterBudget } from './waterBudget'
import './waterBudget.css'

export function WaterBudgetPanel({ days, mode }: { days: WaterDay[]; mode: 'forecast' | 'historical' }) {
  const { t, number, shortDate } = useLocale()
  const [windowDays, setWindowDays] = useState(0)
  const [selectedDate, setSelectedDate] = useState<string>()
  const [width, setWidth] = useState(640)
  const container = useRef<HTMLDivElement>(null)
  const headingId = useId()
  const orderedDays = useMemo(() => [...days].sort((a, b) => a.date.localeCompare(b.date)), [days])
  const budget = useMemo(() => cumulativeWaterBudget(windowDays ? orderedDays.slice(-windowDays) : orderedDays), [orderedDays, windowDays])

  useEffect(() => {
    if (!container.current) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.round(entry.contentRect.width))
    })
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [])

  const points = budget.kind === 'available' ? budget.points : []
  const selectedIndex = selectedDate === undefined ? points.length - 1 : Math.max(0, points.findIndex((point) => point.date === selectedDate))
  const selected = points[selectedIndex]
  const compact = width < 560
  const left = compact ? 37 : 49
  const right = 13
  const height = compact ? 175 : 208
  const top = 12, bottom = height - 29
  const axis = axisRange(0, Math.max(0, ...points.flatMap((point) => [point.incoming, point.outgoing])), 1)
  const x = (index: number) => left + (index + 1) / Math.max(1, points.length) * (width - left - right)
  const y = (value: number) => bottom - value / axis.max * (bottom - top)
  const line = (key: 'incoming' | 'outgoing') => `${left},${y(0)} ` + points.map((point, index) => `${x(index)},${y(point[key])}`).join(' ')
  const ticks = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])].filter((index) => index >= 0)

  return (
    <section className="water-budget" aria-labelledby={headingId}>
      <div className="water-budget-heading">
        <div><h3 id={headingId}>{t('Cumulative water balance')}</h3>
          <p>{t(mode === 'forecast' ? 'Forecast totals for the remaining days, not measured water use.' : 'Historical weather with a modelled soil-water balance.')}</p></div>
        {orderedDays.length > 7 && <label className="water-budget-window"><span className="sr-only">{t('Balance period')}</span>
          <select value={windowDays} onChange={(event) => { setWindowDays(Number(event.target.value)); setSelectedDate(undefined) }}>
            <option value={0}>{t('Entire period')}</option><option value={7}>{t('Last 7 days')}</option>
            {orderedDays.length > 30 && <option value={30}>{t('Last 30 days')}</option>}
          </select></label>}
      </div>
      <div ref={container} className="water-budget-content">
        {budget.kind === 'unavailable' ? <p className="water-budget-unavailable" role="status">{t(budget.reason === 'empty'
          ? 'No water-balance days available.' : 'This period has missing or inconsistent balance data. Update the calculation.')}</p> : selected && <>
          <div className="water-budget-summary">
            <div><span>{t('Change in reserve')}</span><strong data-loss={budget.totals.change < 0}>{number(budget.totals.change, 1, true)} <small>{t('mm')}</small></strong></div>
            <p><span>{shortDate(points[0].date)} – {shortDate(budget.totals.date)}</span>
              <span>{t('Opening reserve')} <b>{number(budget.opening)} {t('mm')}</b><i aria-hidden="true">→</i>{t('Closing reserve')} <b>{number(budget.closing)} {t('mm')}</b></span></p>
          </div>
          <figure className="water-budget-figure">
            <figcaption><span><i className="water-budget-in" />{t('Rain + irrigation')}</span><span><i className="water-budget-out" />{t('Crop water use + drainage')}</span><span>{t('mm')}</span></figcaption>
            <svg viewBox={`0 0 ${width} ${height}`} height={height} role="img"
              aria-label={`${t('Cumulative incoming and outgoing water')}: ${number(budget.totals.incoming)} / ${number(budget.totals.outgoing)} ${t('mm')}`}>
              {axis.ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="water-chart-grid" />
                <text x={left - 8} y={y(tick) + 4} textAnchor="end">{number(tick, 0)}</text></g>)}
              <polyline points={line('incoming')} fill="none" stroke="#0d0d0d" strokeWidth="2.5" strokeLinejoin="round" />
              <polyline points={line('outgoing')} fill="none" stroke="#8a8a8a" strokeWidth="2.5" strokeDasharray="7 4" strokeLinejoin="round" />
              <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={top} y2={bottom} stroke="#c4c4c4" strokeDasharray="4 4" />
              {(['incoming', 'outgoing'] as const).map((key) => <circle key={key} cx={x(selectedIndex)} cy={y(selected[key])} r="4" fill={key === 'incoming' ? '#0d0d0d' : '#8a8a8a'} stroke="#fff" strokeWidth="1.5" />)}
              {ticks.map((index) => <text key={index} x={x(index)} y={height - 6} textAnchor={index === points.length - 1 ? 'end' : 'middle'}>{shortDate(points[index].date)}</text>)}
            </svg>
          </figure>
          {points.length > 1 && <label className="water-budget-slider"><span>{t('Cumulative through')} <strong>{shortDate(selected.date)}</strong></span>
            <input type="range" min={0} max={points.length - 1} step={1} value={selectedIndex}
              aria-label={t('Cumulative balance day')} aria-valuetext={shortDate(selected.date)}
              onChange={(event) => setSelectedDate(points[Number(event.target.value)].date)} /></label>}
          <dl className="water-budget-fluxes" aria-live="polite">
            {([{ key: 'rain', label: 'Rain', sign: '+' }, { key: 'irrigation', label: 'Irrigation', sign: '+' },
              { key: 'use', label: 'Crop water use', sign: '−' }, { key: 'drainage', label: 'Modelled drainage', sign: '−' }] as const).map(({ key, label, sign }) => <div key={key}>
              <dt>{t(label)}</dt><dd><span aria-hidden="true">{sign}</span>{number(selected[key])} <small>{t('mm')}</small></dd>
            </div>)}
          </dl>
          <p className="water-budget-note">{t('Drainage is a bucket-model estimate. Surface runoff is not calculated here.')}</p>
        </>}
      </div>
    </section>
  )
}
