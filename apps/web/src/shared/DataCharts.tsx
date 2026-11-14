import { useEffect, useId, useRef, useState } from 'react'
import { useLocale } from './locale'

type Day = {
  date: string; values: (number | null)[]; rain: number; et0: number | null
  interval?: [number, number]; detail?: string
}
type Series = { label: string; color: string }
// series colours (SVG attributes cannot read CSS variables): --series-1..3 from styles.css, then --ink-2
export const chartColors = ['#0d0d0d', '#8a8a8a', '#c4c4c4', '#5c5c5c']
const rainColor = '#0d0d0d'
const etColor = '#8a8a8a'
const dangerColor = '#b42318'
const axisLeft = 49
const axisRight = 10
const moistureTop = 14
const moistureBottom = 141
const weatherTop = 190
const weatherBottom = 248

export function axisRange(low: number, high: number, minimumSpan: number, tickCount = 3) {
  const span = Math.max(high - low, minimumSpan)
  const stepSize = span / tickCount
  const magnitude = 10 ** Math.floor(Math.log10(stepSize))
  const step = ([1, 2, 2.5, 5, 10].find((factor) => factor * magnitude >= stepSize) ?? 10) * magnitude
  const start = Math.max(0, Math.floor((low - span * .08) / step))
  const end = Math.max(start + 1, Math.ceil((high + span * .08) / step))
  return { min: start * step, max: end * step, ticks: Array.from({ length: end - start + 1 }, (_, i) => (start + i) * step) }
}

export function ForecastTrend({ days, series, unit, threshold, initialDate }: {
  days: Day[]; series: Series[]; unit: string; threshold?: number; initialDate?: string
}) {
  const { t, number, date, shortDate } = useLocale()
  const [selectedDate, setSelectedDate] = useState(initialDate)
  const chart = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(560)
  const area = useId()

  useEffect(() => {
    if (!chart.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(chart.current)
    return () => observer.disconnect()
  }, [])

  const selectedIndex = days.findIndex((day) => day.date === selectedDate)
  const index = Math.max(0, selectedIndex)
  const selected = days[index]
  if (!selected) return null
  const numeric = days.flatMap((day) => day.values).filter((value): value is number => value !== null)
  if (threshold !== undefined) numeric.push(threshold)
  const decimals = unit === 'mm' ? 1 : 3
  const primaryAxis = axisRange(Math.min(...numeric), Math.max(...numeric), unit === 'mm' ? 5 : .01)
  const weatherAxis = axisRange(0, Math.max(...days.flatMap(({ rain, et0 }) => [rain, et0 ?? 0])), 1)
  const column = Math.max(1, width - axisLeft - axisRight) / days.length
  const x = (dayIndex: number) => axisLeft + column * (dayIndex + .5)
  const y = (value: number) => moistureBottom - (moistureBottom - moistureTop) * (value - primaryAxis.min) / (primaryAxis.max - primaryAxis.min)
  const weatherY = (value: number) => weatherBottom - (weatherBottom - weatherTop) * value / weatherAxis.max
  const barWidth = Math.min(13, column * .22)
  const primary = days.map((day) => day.values[0])
  const areaPoints = primary.every((value) => value != null)
    ? primary.map((value, i) => x(i) + ',' + y(value!)).join(' ') : undefined
  const belowThreshold = (value: number) => threshold !== undefined && value < threshold

  return (
    <div className="forecast-visual">
      <figure className="forecast-plot">
        <figcaption><strong>{series[0].label}</strong><span>{t(unit)}</span></figcaption>
        <div ref={chart} className="forecast-canvas">
          <svg viewBox={'0 0 ' + width + ' 254'} height="254" role="img" aria-label={series.map(({ label }) => label).join(', ') + ' · ' + t('Rain and ET₀')}>
            <defs><linearGradient id={area} x1="0" x2="0" y1="0" y2="1" gradientUnits="objectBoundingBox">
              <stop stopColor="#0d0d0d" stopOpacity=".1" />
              <stop offset="1" stopColor="#0d0d0d" stopOpacity=".01" />
            </linearGradient></defs>
            {threshold !== undefined && <rect x={axisLeft} y={y(threshold)} width={Math.max(0, width - axisLeft - axisRight)}
              height={moistureBottom - y(threshold)} fill={dangerColor} fillOpacity=".06" />}
            <rect x={x(index) - column / 2 + 2} y="4" width={Math.max(0, column - 4)} height="244" rx="8" fill="#e2e2e2" />
            {primaryAxis.ticks.map((tick) => (
              <g key={tick}>
                <line x1={axisLeft} x2={width - axisRight} y1={y(tick)} y2={y(tick)} className="forecast-gridline" />
                <text x={axisLeft - 8} y={y(tick) + 4} textAnchor="end">{number(tick, decimals)}</text>
              </g>
            ))}
            {areaPoints && <polygon points={x(0) + ',' + moistureBottom + ' ' + areaPoints + ' ' + x(days.length - 1) + ',' + moistureBottom} fill={'url(#' + area + ')'} />}
            {threshold !== undefined && <line x1={axisLeft} x2={width - axisRight} y1={y(threshold)} y2={y(threshold)}
              stroke={dangerColor} strokeDasharray="5 4" strokeWidth="1.5" />}
            <line x1={x(index)} x2={x(index)} y1="4" y2={moistureBottom} stroke="#8a8a8a" strokeDasharray="3 4" />
            {series.map(({ label, color }, seriesIndex) => (
              <g key={label}>
                {days.map((day, i) => {
                  const value = day.values[seriesIndex]
                  const prior = days[i - 1]?.values[seriesIndex]
                  if (value == null) return null
                  return <g key={day.date}>
                    {prior != null && <line x1={x(i - 1)} y1={y(prior)} x2={x(i)} y2={y(value)}
                      stroke={color} strokeWidth="2.5" strokeLinecap="round" />}
                    <circle cx={x(i)} cy={y(value)} r={day === selected ? 5 : 3.5}
                      fill={seriesIndex === 0 && belowThreshold(value) ? dangerColor : color} stroke="#fff" strokeWidth="1.6" />
                  </g>
                })}
              </g>
            ))}
            <line x1="0" x2={width} y1="161" y2="161" className="forecast-gridline" />
            <text x="0" y="180" className="forecast-axis-title">{t('Rain and ET₀')} · {t('mm')}</text>
            {[0, weatherAxis.max].map((tick) => (
              <g key={tick}>
                <line x1={axisLeft} x2={width - axisRight} y1={weatherY(tick)} y2={weatherY(tick)} className="forecast-gridline" />
                <text x={axisLeft - 8} y={weatherY(tick) + 4} textAnchor="end">{number(tick)}</text>
              </g>
            ))}
            {days.map(({ date: day, rain, et0 }, i) => (
              <g key={day}>
                <rect x={x(i) - barWidth - 2} y={weatherY(rain)} width={barWidth} height={weatherBottom - weatherY(rain)} rx="2" fill={rainColor} />
                {et0 !== null && <rect x={x(i) + 2} y={weatherY(et0)} width={barWidth} height={weatherBottom - weatherY(et0)} rx="2" fill={etColor} />}
                <rect className="forecast-hit" x={axisLeft + column * i} y="4" width={column} height="244" fill="transparent"
                  onClick={() => setSelectedDate(day)} />
              </g>
            ))}
          </svg>
          <div className="forecast-dates" role="group" aria-label={t('Forecast day')} style={{ paddingInline: axisLeft + 'px ' + axisRight + 'px' }}>
            {days.map((day) => <button key={day.date} type="button" aria-label={date(day.date)}
              aria-pressed={day === selected} onClick={() => setSelectedDate(day.date)}>{shortDate(day.date)}</button>)}
          </div>
        </div>
        <ul className="chart-key forecast-key" aria-label={t('Chart legend')}>
          {series.map(({ label, color }) => <li key={label}><i className="forecast-line-key" style={{ background: color }} />{label}</li>)}
          {threshold !== undefined && <li><i className="stress-key" />{t('Stress threshold')}</li>}
          <li><i style={{ background: rainColor }} />{t('Rain')}</li>
          <li><i style={{ background: etColor }} />ET₀</li>
        </ul>
      </figure>
      <aside className="forecast-readout" aria-live="polite">
        <div className="forecast-readout-date"><span>{t('Forecast day')}</span><time dateTime={selected.date}>{date(selected.date)}</time></div>
        <dl>
          {series.map(({ label }, i) => <div key={label} className={i === 0 ? 'forecast-main-value' : undefined}>
            <dt>{label}</dt><dd>{selected.values[i] == null ? t('Unavailable') : <>{number(selected.values[i], decimals)} <small>{t(unit)}</small></>}</dd>
          </div>)}
          <div><dt>{t('Rain')}</dt><dd>{number(selected.rain)} {t('mm')}</dd></div>
          <div><dt>ET₀</dt><dd>{selected.et0 == null ? t('Unavailable') : number(selected.et0) + ' ' + t('mm')}</dd></div>
          {selected.detail && <div className="forecast-value-wide"><dt>{t('Model and depth')}</dt><dd>{selected.detail}</dd></div>}
          {selected.interval && <div className="forecast-value-wide"><dt>{t('Prediction interval')}</dt><dd>
            {selected.interval.map((value) => number(value, decimals)).join('–')} {t(unit)}
          </dd></div>}
        </dl>
      </aside>
    </div>
  )
}

export function ComparisonBars({ groups }: {
  groups: { label: string; unit: string; values: { label: string; value: number | null }[] }[]
}) {
  const { t, number } = useLocale()
  return (
    <div className="comparison-charts">
      {groups.map(({ label, unit, values }) => {
        const max = Math.max(1, ...values.map(({ value }) => value ?? 0))
        return <figure className="comparison-chart" key={label}>
          <figcaption>{label}</figcaption>
          <ul>{values.map((item, index) => <li key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value === null ? t('Not supplied') : number(item.value) + ' ' + unit}</strong>
            <div className="comparison-track" aria-hidden="true">
              {item.value !== null && <i style={{ width: item.value / max * 100 + '%', background: chartColors[index % chartColors.length] }} />}
            </div>
          </li>)}</ul>
        </figure>
      })}
    </div>
  )
}
