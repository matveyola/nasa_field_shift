import { useCallback, useId, useMemo, useState } from 'react'
import { getSeasonalWeather, getWeatherForecast, type SeasonalWeather, type WeatherForecast } from '../../api/weather'
import type { Field } from '../../api/contracts'
import { axisRange } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { seasonalWeatherMetrics } from './seasonalWeatherMetrics'
import './seasonalWeather.css'

type Props = { field: Field; season: number }
type ChartSeries = { label: string; color: string; values: (number | null)[] }
type WeatherChartProps = {
  weather: SeasonalWeather | WeatherForecast
  selected: number
  onSelect: (index: number) => void
}

const plot = { left: 40, right: 313, top: 12, bottom: 112 }
const width = 320
const height = 138

function WeatherChart({ weather, title, unit, series, selected, onSelect }: WeatherChartProps & {
  title: string
  unit: string
  series: ChartSeries[]
}) {
  const { language, t, number, shortDate } = useLocale()
  const id = useId()
  const numeric = series.flatMap(({ values }) => values.filter((value): value is number => value !== null))
  const low = Math.floor(Math.min(0, ...numeric) / 5) * 5
  const scale = axisRange(0, Math.max(0, ...numeric) - low, 1)
  const season = 'season' in weather ? weather.season : undefined
  const dayMs = 86_400_000
  const start = season ? Date.UTC(season, 0, 1) : Date.parse(weather.start_date) - dayMs / 2
  const end = season ? Date.UTC(season + 1, 0, 1) : Date.parse(weather.end_date) + dayMs / 2
  const x = (time: number) => plot.left + (time - start) / (end - start) * (plot.right - plot.left)
  const y = (value: number) => plot.bottom - (value - low) / scale.max * (plot.bottom - plot.top)
  const times = weather.days.map(({ date }) => Date.parse(date))
  const selectedX = x(times[selected])
  const month = new Intl.DateTimeFormat(language === 'uk' ? 'uk-UA' : 'en', { month: 'short', timeZone: 'UTC' })
  const labels = season ? [0, 2, 4, 6, 8, 10].map((index) => {
    const time = Date.UTC(season, index, 15)
    return { time, label: month.format(time) }
  }) : weather.days.map(({ date }) => ({ time: Date.parse(date), label: shortDate(date) }))

  return <figure className="seasonal-weather-chart">
    <figcaption><strong>{title}</strong><span>{unit}</span></figcaption>
    {numeric.length ? <>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={id}>
        <title id={id}>{title} · {unit}</title>
        {scale.ticks.map((tick) => <g key={tick}>
          <line x1={plot.left} x2={plot.right} y1={y(tick + low)} y2={y(tick + low)} className="forecast-gridline" />
          <text x={plot.left - 6} y={y(tick + low) + 3} textAnchor="end">{number(tick + low, 1)}</text>
        </g>)}
        {labels.map(({ time, label }) => <text key={time} x={x(time)} y={height - 6} textAnchor="middle">{label}</text>)}
        <line x1={selectedX} x2={selectedX} y1={plot.top} y2={plot.bottom} style={{ stroke: 'var(--ink-3)' }} strokeDasharray="3 4" />
        {series.map(({ label, color, values }) => {
          const path = values.map((value, i) => {
            if (value === null) return ''
            const point = `${x(times[i])},${y(value)}`
            return i > 0 && values[i - 1] !== null ? `L${point}` : `M${point}L${point}`
          }).join(' ')
          const current = values[selected]
          return <g key={label}>
            <path d={path} fill="none" style={{ stroke: color }} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            {current !== null && <circle cx={selectedX} cy={y(current)} r="3.5" style={{ fill: color }} stroke="#fff" strokeWidth="1.3" />}
          </g>
        })}
        <rect className="seasonal-weather-chart-hit" x={plot.left} y={plot.top} width={plot.right - plot.left}
          height={plot.bottom - plot.top} fill="transparent" aria-hidden="true" onClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect()
            const time = start + (event.clientX - rect.left) / rect.width * (end - start)
            const index = Math.round((time - times[0]) / dayMs)
            onSelect(Math.max(0, Math.min(index, times.length - 1)))
          }} />
      </svg>
      {series.length > 1 && <ul className="seasonal-weather-chart-key" aria-label={t('Chart legend')}>
        {series.map(({ label, color }) => <li key={label}><i style={{ background: color }} />{label}</li>)}
      </ul>}
    </> : <p className="analysis-empty">{t('Unavailable')}</p>}
  </figure>
}

function TemperatureChart(props: WeatherChartProps) {
  const { t } = useLocale()
  return <WeatherChart {...props} title={t('Air temperature')} unit="°C"
    series={[
      { label: t('Daily high'), color: 'var(--series-1)', values: props.weather.days.map((point) => point.temperature_max_c) },
      { label: t('Daily low'), color: 'var(--series-2)', values: props.weather.days.map((point) => point.temperature_min_c) },
    ]} />
}

function WeatherDayReadout({ day, dailyDegreeDays }: {
  day: SeasonalWeather['days'][number]
  dailyDegreeDays?: number | null
}) {
  const { t, number } = useLocale()
  const measure = (value: number | null, unit: string) => value === null ? t('Unavailable') : `${number(value)} ${unit}`
  return <dl className="seasonal-weather-day-readout">
    <div><dt>{t('Daily low')}</dt><dd>{measure(day.temperature_min_c, '°C')}</dd></div>
    <div><dt>{t('Daily high')}</dt><dd>{measure(day.temperature_max_c, '°C')}</dd></div>
    <div><dt>{t('Daily precipitation')}</dt><dd>{measure(day.precipitation_mm, t('mm'))}</dd></div>
    {dailyDegreeDays !== undefined && <div><dt>{t('Daily GDD')}</dt><dd>{measure(dailyDegreeDays, '°C·d')}</dd></div>}
  </dl>
}

function WeatherCharts({ weather }: { weather: SeasonalWeather }) {
  const { t, number, date } = useLocale()
  const [base, setBase] = useState(10)
  const [chosenDate, setChosenDate] = useState<string>()
  const metrics = useMemo(() => seasonalWeatherMetrics(weather.days, base), [weather.days, base])
  const selected = weather.days.findIndex((day) => day.date === chosenDate)
  const index = selected < 0 ? weather.days.length - 1 : selected
  const day = weather.days[index]
  const daily = metrics.points[index]
  const onSelect = (position: number) => setChosenDate(weather.days[position].date)
  const measure = (value: number | null, unit: string) => value === null ? t('Unavailable') : `${number(value)} ${unit}`
  return <>
    <p className="analysis-description">{date(weather.start_date)} – {date(weather.end_date)} · {weather.timezone}</p>
    <dl className="seasonal-weather-totals">
      <div><dt>{t('Available-day precipitation')}</dt><dd>{measure(metrics.precipitation, t('mm'))}</dd>
        <small>{metrics.precipitationDays}/{weather.days.length} {t('days with data')}</small></div>
      <div><dt>{t('Available-day GDD')}</dt><dd>{measure(metrics.degreeDays, '°C·d')}</dd>
        <small>{metrics.temperatureDays}/{weather.days.length} {t('days with data')}</small></div>
    </dl>
    <label className="seasonal-weather-base">{t('GDD base temperature')}
      <select value={base} onChange={(event) => setBase(Number(event.target.value))}>
        {[0, 5, 10, 15].map((value) => <option value={value} key={value}>{value} °C</option>)}
      </select>
    </label>
    <TemperatureChart weather={weather} selected={index} onSelect={onSelect} />
    <WeatherChart weather={weather} title={t('Accumulated precipitation')} unit={t('mm')} selected={index} onSelect={onSelect}
      series={[{ label: t('Accumulated precipitation'), color: 'var(--series-1)', values: metrics.points.map((point) => point.cumulativePrecipitation) }]} />
    <WeatherChart weather={weather} title={t('Accumulated growing degree-days')} unit="°C·d" selected={index} onSelect={onSelect}
      series={[{ label: t('Accumulated growing degree-days'), color: 'var(--series-1)', values: metrics.points.map((point) => point.cumulativeDegreeDays) }]} />
    <label className="seasonal-weather-day">
      <span>{t('Weather day')} · <time dateTime={day.date}>{date(day.date)}</time></span>
      <input type="range" min="0" max={weather.days.length - 1} value={index} disabled={weather.days.length === 1}
        aria-valuetext={date(day.date)} onChange={(event) => onSelect(Number(event.target.value))} />
    </label>
    <WeatherDayReadout day={day} dailyDegreeDays={daily.dailyDegreeDays} />
    <details className="analysis-quality"><summary>{t('Source and limitations')}</summary>
      <p>{t('Daily ERA5 reanalysis grid estimates at the field centroid. The archive is shown with a seven-day delay. Precipitation includes rain and snow water equivalent.')}</p>
      <p>{t('Daily GDD = max(0, (daily high + daily low) / 2 − base). Accumulation starts January 1. Choose a base appropriate to the intended analysis.')}</p>
      <p>{t('Blank segments mark missing values. Cumulative lines sum available days only.')}</p>
      <a className="analysis-source" href={weather.source_url} target="_blank" rel="noreferrer">{weather.source} · {number(weather.resolution_degrees, 2)}°</a>
    </details>
  </>
}

function SeasonalWeatherContent({ field, season }: Props) {
  const { t } = useLocale()
  const load = useCallback((signal: AbortSignal) => getSeasonalWeather(field.id, season, signal), [field.id, season])
  const { data, error, pending, reload } = useResource(load)
  if (error) return <ErrorBox message={error} retry={reload} />
  if (pending) return <p className="analysis-empty" role="status">{t('Loading seasonal weather…')}</p>
  if (!data?.days.length) return <p className="analysis-empty">{t('No archived weather is available for this season yet.')}</p>
  if (data.boundary_revision !== field.boundary_revision) return <ErrorBox
    message="The field boundary changed. Reload the field before viewing weather." retry={reload} />
  return <WeatherCharts weather={data} />
}

function ForecastCharts({ weather }: { weather: WeatherForecast }) {
  const { t, date, shortDate } = useLocale()
  const [index, setIndex] = useState(0)
  return <>
    <p className="analysis-description">{date(weather.start_date)} – {date(weather.end_date)} · {weather.timezone}</p>
    <TemperatureChart weather={weather} selected={index} onSelect={setIndex} />
    <WeatherChart weather={weather} title={t('Daily precipitation')} unit={t('mm')} selected={index} onSelect={setIndex}
      series={[{ label: t('Daily precipitation'), color: 'var(--series-1)', values: weather.days.map((day) => day.precipitation_mm) }]} />
    <div className="ndvi-observation-dates" role="group" aria-label={t('Forecast day')}>
      {weather.days.map((day, position) => <button type="button" key={day.date} aria-label={date(day.date)}
        aria-pressed={position === index} onClick={() => setIndex(position)}>{shortDate(day.date)}</button>)}
    </div>
    <WeatherDayReadout day={weather.days[index]} />
    <p className="analysis-footnote">{t('Model forecast at the field centroid. Days use the field’s local time.')}</p>
    <p className="analysis-footnote">{t('Retrieved')} · {date(weather.fetched_at, true)} UTC</p>
    <a className="analysis-source" href={weather.source_url} target="_blank" rel="noreferrer">{weather.source}</a>
  </>
}

function ForecastContent({ field }: { field: Field }) {
  const { t } = useLocale()
  const load = useCallback((signal: AbortSignal) => getWeatherForecast(field.id, signal), [field.id])
  const { data, error, pending, reload } = useResource(load, 900_000)
  if (error) return <ErrorBox message={error} retry={reload} />
  if (pending) return <p className="analysis-empty" role="status">{t('Loading weather forecast…')}</p>
  if (!data?.days.length) return <p className="analysis-empty">{t('Unavailable')}</p>
  if (data.boundary_revision !== field.boundary_revision) return <ErrorBox
    message="The field boundary changed. Reload the field before viewing weather." retry={reload} />
  return <ForecastCharts weather={data} />
}

export function SeasonalWeatherPanel({ field, season }: Props) {
  const { t } = useLocale()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [forecastOpen, setForecastOpen] = useState(false)
  const fieldKey = `${field.id}:${field.boundary_revision}`
  return <>
    <section className="seasonal-weather-panel" aria-label={t('Seasonal weather')}>
      <details onToggle={(event) => setHistoryOpen(event.currentTarget.open)}>
        <summary><strong>{t('Seasonal weather')}</strong><span>{season}</span></summary>
        {historyOpen && <SeasonalWeatherContent key={`${fieldKey}:${season}`} field={field} season={season} />}
      </details>
    </section>
    <section className="seasonal-weather-panel" aria-label={t('7-day weather forecast')}>
      <details onToggle={(event) => setForecastOpen(event.currentTarget.open)}>
        <summary><strong>{t('7-day weather forecast')}</strong></summary>
        {forecastOpen && <ForecastContent key={fieldKey} field={field} />}
      </details>
    </section>
  </>
}
