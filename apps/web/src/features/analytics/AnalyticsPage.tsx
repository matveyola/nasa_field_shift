import { useCallback, useState, type FormEvent } from 'react'
import type { Field, FieldContext } from '../../api/contracts'
import { fieldWeatherApi, type DashboardMetric, type DashboardSpan, type WeatherDashboard } from '../../api/fieldWeather'
import type { AnalyticsPageProps } from '../../shared/productContext'
import { useAccess } from '../access/AccessProvider'
import { ErrorBox, Loading } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { DashboardChart, useDashboardLabels } from './DashboardChart'
import { isDay, periodDays, shiftDay, weatherTotals, type Period } from './dashboardModel'
import { WaterDashboard } from './WaterDashboard'
import './dashboard.css'

export function AnalyticsPage(props: AnalyticsPageProps) {
  return <AnalyticsWorkspace key={props.forecastDay ?? 'dashboard'} {...props} />
}

function AnalyticsWorkspace({ field, fields, context, chooseField, addField, forecastDay, jobId, openAgent }: AnalyticsPageProps) {
  const { label } = useDashboardLabels()
  const { canEdit } = useAccess()
  const today = new Date().toISOString().slice(0, 10)
  const defaults = { start: today, end: shiftDay(today, 29) }
  const linked = forecastDay && isDay(forecastDay) ? { start: forecastDay, end: forecastDay } : defaults
  const [selected, setSelected] = useState<Period>(linked)
  const [draft, setDraft] = useState<Period>(linked)
  const [invalid, setInvalid] = useState(false)
  function select(period: Period) {
    setSelected(period)
    setDraft(period)
    setInvalid(false)
  }

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!isDay(draft.start) || !isDay(draft.end) || periodDays(draft) < 1 || periodDays(draft) > 92) {
      setInvalid(true)
      return
    }
    select(draft)
  }

  return <div className="page-body field-dashboard">
    <header><h1>{label('Analytics', 'Аналітика')}</h1></header>
    <form className="dashboard-period" onSubmit={apply}>
      <label className="control dashboard-field">
        <span>{label('Field', 'Поле')}</span>
        <select value={field?.id ?? ''} onChange={event => chooseField(event.target.value)}>
          <option value="">{label('Select a field', 'Оберіть поле')}</option>
          {fields.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="control dashboard-date"><span>{label('Start date', 'Початок періоду')}</span>
        <input type="date" min="1900-01-01" max="2200-12-31" required disabled={!field} value={draft.start}
          onChange={event => { setDraft(old => ({ ...old, start: event.target.value })); setInvalid(false) }} />
      </label>
      <label className="control dashboard-date"><span>{label('End date', 'Кінець періоду')}</span>
        <input type="date" min="1900-01-01" max="2200-12-31" required disabled={!field} value={draft.end}
          onChange={event => { setDraft(old => ({ ...old, end: event.target.value })); setInvalid(false) }} />
      </label>
      <button type="submit" className="button primary" disabled={!field}>{label('Apply', 'Застосувати')}</button>
      <button type="button" className="button subtle" disabled={!field} onClick={() => select(defaults)}>{label('Next 30 days', 'Найближчі 30 днів')}</button>
    </form>
    {invalid && <p role="alert">{label('Select 1–92 days in date order.', 'Оберіть від 1 до 92 днів у порядку дат.')}</p>}
    {!field || !context ? <section className="dashboard-card">
      <p>{fields.length ? label('Choose a field to view its data.', 'Оберіть поле, щоб переглянути його дані.') : label('Add a field to view analytics.', 'Додайте поле, щоб переглянути аналітику.')}</p>
      {!fields.length && canEdit && <button className="button" type="button" onClick={addField}>{label('Add field', 'Додати поле')}</button>}
    </section> : <DashboardContent
      key={[field.id, field.boundary_revision, context.season, context.scenario, jobId, today, selected.start, selected.end].join(':')}
      field={field} context={context} today={today} period={selected} jobId={jobId} choosePeriod={select} openAgent={openAgent} />}
  </div>
}

function DashboardContent({ field, context, today, period, jobId, choosePeriod, openAgent }: {
  field: Field; context: FieldContext; today: string; period: Period; jobId?: string
  choosePeriod: (period: Period) => void; openAgent: (jobId?: string) => void
}) {
  const { label, dates, number, source } = useDashboardLabels()
  const load = useCallback((signal: AbortSignal) =>
    fieldWeatherApi.dashboard(field.id, field.boundary_revision, period.start, period.end, signal),
  [field.id, field.boundary_revision, period.start, period.end])
  const weather = useResource(load)
  const inScope = weather.data?.field_id === field.id && weather.data.boundary_revision === field.boundary_revision &&
    weather.data.start === period.start && weather.data.end === period.end
  const data = inScope ? weather.data : undefined
  const rains = weatherTotals(data?.series.precipitation ?? [])
  const hottest = maximum(data?.series.temperature_max ?? [])
  const moisture = data?.series.surface_moisture.flatMap(span => span.points).at(-1)
  return <div className="dashboard-body">
    {weather.pending && <Loading text={label('Loading field metrics…', 'Завантажуємо показники поля…')} />}
    {!weather.pending && !data && <ErrorBox message={label('Field weather is temporarily unavailable.', 'Погодні дані поля тимчасово недоступні.')} retry={weather.reload} />}
    {data && <div className="dashboard-cards">
      <article className="dashboard-card">
        <h2>{label('Precipitation · period totals', 'Опади · суми за період')}</h2>
        {!rains.length && <p>{label('No data for these dates', 'Немає даних для цих дат')}</p>}
        {rains.map(span => <div key={span.start}><strong>{number(span.total)} {label('mm', 'мм')}</strong>
          <p>{source(span.source)} · {dates(span)}</p></div>)}
        {rains.length > 0 && rains.reduce((count, span) => count + periodDays(span), 0) < periodDays(period) &&
          <p>{label('Partial coverage', 'Часткове покриття')}</p>}
      </article>
      <article className="dashboard-card">
        <h2>{label('Peak of daily maximum temperatures', 'Найвища з добових максимальних температур')}</h2>
        {hottest ? <>
          <strong>{number(hottest.point.value)} °C</strong>
          <p>{source(hottest.source)} · {dates(hottest.point)}</p>
        </> : <p>{label('No operational or historical data', 'Немає оперативних чи історичних даних')}</p>}
      </article>
      <article className="dashboard-card">
        <h2>{label('Surface soil moisture · 0–7 cm', 'Поверхнева вологість ґрунту · 0–7 см')}</h2>
        {moisture ? <><strong>{number(moisture.value, 3)} {label('m³/m³', 'м³/м³')}</strong>
          <p>{source('reanalysis')} · {dates(moisture)}</p></> : <p>{label('No historical value for these dates', 'Немає історичного значення для цих дат')}</p>}
      </article>
    </div>}
    <WaterDashboard field={field} context={context} today={today} period={period} jobId={jobId}
      choosePeriod={choosePeriod} openAgent={openAgent} />
    {data && <WeatherCharts data={data} period={period} />}
  </div>
}

function maximum(spans: DashboardSpan[]) {
  const points = spans.flatMap(({ source, points }) => source && source !== 'climate'
    ? points.map(point => ({ point, source })) : [])
  return points.reduce<typeof points[number] | undefined>((peak, value) => !peak || value.point.value > peak.point.value ? value : peak, undefined)
}

function WeatherCharts({ data, period }: { data: WeatherDashboard; period: Period }) {
  const { label, source, date } = useDashboardLabels()
  const hasClimate = Object.values(data.series).some(series => series.some(span => span.source === 'climate'))
  const rainUnit = label('mm/day', 'мм/добу')
  const charts: { id: DashboardMetric; title: string; unit: string; digits?: number }[] = [
    { id: 'precipitation', title: label('Precipitation', 'Опади'), unit: rainUnit },
    { id: 'reference_et0', title: label('Reference evapotranspiration · ET₀', 'Еталонна евапотранспірація · ET₀'), unit: rainUnit },
    { id: 'temperature_min', title: label('Daily minimum air temperature', 'Добова мінімальна температура повітря'), unit: '°C' },
    { id: 'temperature_max', title: label('Daily maximum air temperature', 'Добова максимальна температура повітря'), unit: '°C' },
    { id: 'surface_moisture', title: label('Surface soil moisture · 0–7 cm', 'Поверхнева вологість ґрунту · 0–7 см'), unit: label('m³/m³', 'м³/м³'), digits: 3 },
  ]
  const gaps = {
    archive_lag: label('No data · archive publishes with a 7-day lag', 'Немає даних · архів публікується із затримкою 7 днів'),
    outside_coverage: label('No data · outside source coverage', 'Немає даних · поза покриттям джерела'),
    unsupported: label('No data · no compatible source', 'Немає даних · немає сумісного джерела'),
    provider_gap: label('No data · source temporarily unavailable or incomplete', 'Немає даних · джерело тимчасово недоступне або неповне'),
  }
  return <section className="dashboard-weather" aria-labelledby="weather-charts-title">
    <h2 id="weather-charts-title">{label('Weather and surface moisture', 'Погода й поверхнева вологість')}</h2>
    {hasClimate && <p className="dashboard-caption">{label('A long-term climate scenario covers part of this period.', 'Для частини цього періоду використано довгостроковий кліматичний сценарій.')}</p>}
    <div className="dashboard-charts">{charts.map(chart => {
      const spans = data.series[chart.id]
      const describe = (span: DashboardSpan) => span.source ? source(span.source)
        : span.reason ? gaps[span.reason] : label('No data for these dates', 'Немає даних для цих дат')
      const used = new Set(spans.map(item => item.source))
      return <DashboardChart key={chart.id} title={chart.title} unit={chart.unit} digits={chart.digits}
        period={period} spans={spans} describe={describe}
        details={<>
          {used.has('reanalysis') && <p>{source('reanalysis')} · <a href="https://open-meteo.com/en/docs/historical-weather-api">{chart.id === 'surface_moisture' ? 'ERA5-Land' : 'ERA5'}</a></p>}
          {used.has('forecast') && <p>{source('forecast')} · <a href="https://open-meteo.com/en/docs">Open-Meteo</a> · {label('16-day maximum horizon', 'Горизонт до 16 днів')}</p>}
          {used.has('climate') && <p>{source('climate')} · <a href="https://open-meteo.com/en/docs/climate-api">EC-Earth3P-HR · HighResMIP</a>. {label('Simulated averages over intervals of up to seven days. Dates locate the scenario on the chart. This single high-emissions simulation has no uncertainty range.', 'Середні за змодельовані інтервали до семи днів. Дати позначають, де на графіку діє сценарій. Для однієї симуляції з високими викидами діапазону невизначеності немає.')}</p>}
          {used.has(null) && <p>{label('Archive coverage begins in', 'Покриття архіву починається у')} {chart.id === 'surface_moisture' ? '1950' : '1940'}. {
            ['precipitation', 'temperature_min', 'temperature_max'].includes(chart.id)
            && label('The available climate series ends in 2049.', 'Доступний кліматичний ряд закінчується у 2049 році.')
          } {chart.id === 'reference_et0' && label('ET₀ forecast covers up to 16 days.', 'Прогноз ET₀ охоплює до 16 днів.')}</p>}
          {used.size > 1 || !used.has(null) ? <p>{label('Dashboard updated', 'Дашборд оновлено')} · {date(data.generated_at, true)} UTC</p> : null}
        </>} />
    })}</div>
  </section>
}
