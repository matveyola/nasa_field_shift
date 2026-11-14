import { useLocale } from '../../shared/locale'
import type { WaterResult } from '../../api/contracts'

const chart = { width: 960, height: 300, left: 56, right: 24, top: 22, bottom: 45 }
const millisecondsPerDay = 86_400_000

function daysFrom(start: string, day: string) {
  return (Date.parse(day) - Date.parse(start)) / millisecondsPerDay
}

export function WaterChart({
  result, selected, onSelect,
}: { result: WaterResult; selected: string; onSelect: (date: string) => void }) {
  const { t, shortDate } = useLocale()
  const first = result.days[0]
  const last = result.days.at(-1)
  if (!first || !last) return <p>{t('No daily values are available for this run.')}</p>

  const { width, height, left, right, top, bottom } = chart
  const plotBottom = height - bottom
  const duration = Math.max(1, daysFrom(first.date, last.date))
  const minimum = Math.floor(Math.min(result.threshold_mm, ...result.days.map((day) => day.reserve_mm)) / 10) * 10
  const maximum = Math.max(minimum + 10, Math.ceil(Math.max(result.threshold_mm, ...result.days.map((day) => day.reserve_mm)) / 10) * 10)
  const x = (date: string) => left + (width - right - left) * daysFrom(first.date, date) / duration
  const y = (reserve: number) => plotBottom - (plotBottom - top) * (reserve - minimum) / (maximum - minimum)
  const tickInterval = Math.max(1, Math.ceil((maximum - minimum) / 50)) * 10
  const yTicks = Array.from({ length: Math.floor((maximum - minimum) / tickInterval) + 1 }, (_, index) => minimum + index * tickInterval)
  const xTicks = result.days.filter((_, index) => index % Math.ceil(result.days.length / 5) === 0 || index === result.days.length - 1)
  const floweringStart = result.flowering_start ? Math.max(left, x(result.flowering_start)) : left
  const floweringEnd = result.flowering_end ? Math.min(width - right, x(result.flowering_end)) : left
  const showFlowering = floweringEnd > floweringStart
  const line = result.days.map((day) => x(day.date) + ',' + y(day.reserve_mm)).join(' ')
  const selectedDay = result.days.find(({ date }) => date === selected) ?? first

  return (
    <div className="chart-wrap">
      <svg className="water-chart" viewBox={'0 0 ' + width + ' ' + height}
        role="img" aria-label={showFlowering
          ? t('Root-zone water reserve by day, with stress threshold and flowering window')
          : t('Root-zone water reserve by day, with stress threshold')}>
        <defs>
          <linearGradient id="reserve-fill" x2="0" y2="1" gradientUnits="objectBoundingBox">
            <stop stopColor="#0d0d0d" stopOpacity=".12" />
            <stop offset="1" stopColor="#0d0d0d" stopOpacity=".02" />
          </linearGradient>
        </defs>
        {showFlowering && (
          <rect x={floweringStart} y={top} width={floweringEnd - floweringStart} height={plotBottom - top}
            rx="6" fill="#e2e2e2" />
        )}
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#e0e0e0" />
            <text x={left - 12} y={y(tick) + 5} textAnchor="end">{tick}</text>
          </g>
        ))}
        <polygon points={left + ',' + plotBottom + ' ' + line + ' ' + x(last.date) + ',' + plotBottom} fill="url(#reserve-fill)" />
        <line x1={left} x2={width - right} y1={y(result.threshold_mm)} y2={y(result.threshold_mm)}
          stroke="#b42318" strokeWidth="2" strokeDasharray="6 6" />
        <polyline points={line} fill="none" stroke="#0d0d0d" strokeWidth="3.5" strokeLinejoin="round" />
        <line x1={x(selectedDay.date)} x2={x(selectedDay.date)} y1={top} y2={plotBottom}
          stroke="#8a8a8a" strokeDasharray="3 6" />
        {result.days.map((day) => (
          <g key={day.date}>
            <circle cx={x(day.date)} cy={y(day.reserve_mm)} r={day.date === selectedDay.date ? 7 : 4}
              fill={day.reserve_mm < result.threshold_mm ? '#b42318' : '#0d0d0d'} stroke="#fff" strokeWidth="1.5" />
            <circle className="chart-hit" cx={x(day.date)} cy={y(day.reserve_mm)} r="13"
              fill="transparent" onClick={() => onSelect(day.date)}>
              <title>{shortDate(day.date)} · {day.reserve_mm} {t('mm')}</title>
            </circle>
          </g>
        ))}
        {xTicks.map((day) => (
          <text x={x(day.date)} y={plotBottom + 29} textAnchor="middle" key={day.date}>{shortDate(day.date)}</text>
        ))}
      </svg>
      <ul className="chart-key" aria-label={t('Chart legend')}>
        <li><i className="reserve-key" />{t('Reserve')}</li>
        <li><i className="stress-key" />{t('Stress threshold')}</li>
        {showFlowering && <li><i className="flowering-key" />{t('Flowering')}</li>}
      </ul>
    </div>
  )
}
