import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/24/outline'
import { useEffect, useId, useRef, useState } from 'react'
import type { WaterForecast } from '../../api/contracts'
import { axisRange, chartColors } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'

type Props = {
  days: WaterForecast['days']
  threshold: number
  initialDate?: string
  showModelEstimate: boolean
}

export function WaterForecastChart({ days, threshold, initialDate, showModelEstimate }: Props) {
  const { language, t, number, date, shortDate } = useLocale()
  const [selectedDate, setSelectedDate] = useState(initialDate)
  const [width, setWidth] = useState(640)
  const container = useRef<HTMLDivElement>(null)
  const fillId = useId()

  useEffect(() => {
    if (!container.current) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.round(entry.contentRect.width))
    })
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [])

  const selectedIndex = Math.max(0, days.findIndex(({ date }) => date === selectedDate))
  const selected = days[selectedIndex]
  if (!selected) return null
  const compact = width < 600
  const left = compact ? 39 : 65
  const right = compact ? 12 : 33
  const plotWidth = Math.max(1, width - left - right)
  const column = plotWidth / days.length
  const x = (index: number) => left + column * (index + .5)
  const height = compact ? 174 : 236
  const bottom = height - 12
  const top = 12
  const values = days.flatMap((day) => showModelEstimate && day.root_zone_estimate_mm != null
    ? [day.reserve_mm, day.root_zone_estimate_mm] : [day.reserve_mm])
  const reserveAxis = axisRange(Math.min(threshold, ...values), Math.max(threshold, ...values), 10, 5)
  const y = (value: number) => bottom - (value - reserveAxis.min) / (reserveAxis.max - reserveAxis.min) * (bottom - top)
  const weatherHeight = compact ? 90 : 115
  const weatherBottom = weatherHeight - 12
  const weatherAxis = axisRange(0, Math.max(...days.flatMap((day) => [day.rain_mm, day.reference_et0_mm ?? 0])), 1)
  const weatherY = (value: number) => weatherBottom - value / weatherAxis.max * (weatherBottom - 10)
  const barWidth = Math.min(compact ? 17 : 34, column * .28)
  const line = days.map((day, index) => `${x(index)},${y(day.reserve_mm)}`).join(' ')
  const selectedUnderThreshold = selected.reserve_mm < threshold
  const selectedDayLabel = new Intl.DateTimeFormat(language === 'uk' ? 'uk-UA' : 'en', {
    day: 'numeric', month: 'long', timeZone: 'UTC',
  }).format(new Date(selected.date))

  function stepDay(direction: number) {
    const next = days[selectedIndex + direction]
    if (next) setSelectedDate(next.date)
  }

  return (
    <div className="water-forecast-chart" ref={container}>
      <figure className="water-trend-figure">
        <figcaption><strong>{t('Reserve')}</strong><span>, {t('mm')}</span>
          {showModelEstimate && <span className="water-model-key"><i />{t('ML estimate')}</span>}
        </figcaption>
        <svg viewBox={`0 0 ${width} ${height}`} height={height} role="img"
          aria-label={t('Root-zone water reserve by day, with stress threshold')}>
          <defs><linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1">
            <stop stopColor="#0d0d0d" stopOpacity=".1" />
            <stop offset="1" stopColor="#0d0d0d" stopOpacity=".01" />
          </linearGradient></defs>
          <rect x={left} y={y(threshold)} width={plotWidth} height={Math.max(0, bottom - y(threshold))} fill="#b42318" fillOpacity=".06" />
          {reserveAxis.ticks.map((tick) => <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="water-chart-grid" />
            <text x={left - 11} y={y(tick) + 4} textAnchor="end">{number(tick)}</text>
          </g>)}
          <polygon points={`${x(0)},${bottom} ${line} ${x(days.length - 1)},${bottom}`} fill={`url(#${fillId})`} />
          <line x1={left} x2={width - right} y1={y(threshold)} y2={y(threshold)}
            stroke="#b42318" strokeWidth="1.4" strokeDasharray="7 5" />
          <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={top} y2={bottom}
            stroke="#8a8a8a" strokeWidth="1.3" strokeDasharray="6 5" />
          <polyline points={line} fill="none" stroke={chartColors[0]} strokeWidth="2.5" strokeLinejoin="round" />
          {days.map((day, index) => {
            const model = showModelEstimate ? day.root_zone_estimate_mm : null
            const previousModel = showModelEstimate ? days[index - 1]?.root_zone_estimate_mm : null
            return <g key={day.date}>
              {model != null && <>
                {previousModel != null && <line x1={x(index - 1)} y1={y(previousModel)} x2={x(index)} y2={y(model)}
                  stroke={chartColors[1]} strokeWidth="2" strokeDasharray="4 3" />}
                <circle cx={x(index)} cy={y(model)} r="3" fill={chartColors[1]} />
              </>}
              <circle cx={x(index)} cy={y(day.reserve_mm)} r={index === selectedIndex ? 5.5 : 4.5}
                fill={index === selectedIndex && selectedUnderThreshold ? '#b42318' : chartColors[0]}
                stroke="#fff" strokeWidth="1.6" />
              <rect className="water-chart-hit" x={left + column * index} y="0" width={column} height={height}
                fill="transparent" onClick={() => setSelectedDate(day.date)} />
            </g>
          })}
        </svg>
      </figure>
      <figure className="water-weather-figure">
        <figcaption>
          <strong>{t('Rain and evaporation')}<span>, {t('mm')}</span></strong>
          <span className="water-weather-legend"><span><i />{t('Rain')}</span><span><i />ET₀</span></span>
        </figcaption>
        <svg viewBox={`0 0 ${width} ${weatherHeight}`} height={weatherHeight} role="img" aria-label={t('Rain and ET₀')}>
          {weatherAxis.ticks.map((tick) => <g key={tick}>
            <line x1={left} x2={width - right} y1={weatherY(tick)} y2={weatherY(tick)} className="water-chart-grid" />
            <text x={left - 11} y={weatherY(tick) + 4} textAnchor="end">{number(tick)}</text>
          </g>)}
          {days.map((day, index) => <g key={day.date}>
            <rect x={x(index) - barWidth - 2} y={weatherY(day.rain_mm)} width={barWidth}
              height={weatherBottom - weatherY(day.rain_mm)} rx="2" fill="#0d0d0d" />
            {day.reference_et0_mm != null && <rect x={x(index) + 2} y={weatherY(day.reference_et0_mm)} width={barWidth}
              height={weatherBottom - weatherY(day.reference_et0_mm)} rx="2" fill="#8a8a8a" />}
            <rect className="water-chart-hit" x={left + column * index} y="0" width={column} height={weatherHeight}
              fill="transparent" onClick={() => setSelectedDate(day.date)} />
          </g>)}
        </svg>
      </figure>
      <div className="water-date-navigation" style={{ paddingInline: `${left}px ${right}px` }}>
        <button className="icon-button water-date-step" type="button" aria-label={t('Previous day')}
          disabled={selectedIndex === 0} onClick={() => stepDay(-1)}><ChevronLeftIcon aria-hidden="true" /></button>
        <div className="forecast-dates water-dates" role="group" aria-label={t('Forecast day')}>
          {days.map((day) => <button key={day.date} type="button" aria-label={date(day.date)}
            aria-pressed={day === selected} onClick={() => setSelectedDate(day.date)}>{shortDate(day.date)}</button>)}
        </div>
        <button className="icon-button water-date-step" type="button" aria-label={t('Next day')}
          disabled={selectedIndex === days.length - 1} onClick={() => stepDay(1)}><ChevronRightIcon aria-hidden="true" /></button>
      </div>
      <aside className="water-day-readout" aria-live="polite">
        <time dateTime={selected.date} aria-label={date(selected.date)}>{selectedDayLabel}</time>
        <dl>
          <div><dt>{t('Reserve')}</dt><dd>{number(selected.reserve_mm)} {t('mm')}</dd></div>
          {showModelEstimate && <div><dt>{t('ML estimate')}</dt><dd>{selected.root_zone_estimate_mm == null
            ? t('Unavailable') : number(selected.root_zone_estimate_mm) + ' ' + t('mm')}</dd></div>}
          <div><dt>{t('Rain')}</dt><dd>{number(selected.rain_mm)} {t('mm')}</dd></div>
          <div><dt>ET₀</dt><dd>{selected.reference_et0_mm == null ? t('Unavailable') : number(selected.reference_et0_mm) + ' ' + t('mm')}</dd></div>
          <div className="water-threshold-readout"><dt>{t('Threshold')}</dt><dd>{number(threshold)} {t('mm')}</dd></div>
        </dl>
      </aside>
    </div>
  )
}
