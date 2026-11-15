import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { Field, FieldContext } from '../../api/contracts'
import type { DashboardSpan } from '../../api/fieldWeather'
import { matchingWaterResult } from '../../shared/analysis'
import { JobStatus } from '../../shared/JobStatus'
import { useJob, useStartJob } from '../../shared/jobs'
import { ErrorBox } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { explicitHistoricalRun } from '../assistant/assistantRun'
import { cumulativeWaterBudget } from '../water/waterBudget'
import { waterReferenceDisplay } from '../water/waterReferenceDisplay'
import { supportsWaterReference } from './currentWater'
import { DashboardChart, useDashboardLabels } from './DashboardChart'
import { contains, droughtWindow, periodDays, reservePoints, shiftDay, type Period } from './dashboardModel'
import { waterMetrics } from './waterMetrics'

export function WaterDashboard({ field, context, today, period, jobId, openAgent, choosePeriod }: {
  field: Field; context: FieldContext; today: string; period: Period; jobId?: string
  openAgent: (jobId?: string) => void; choosePeriod: (period: Period) => void
}) {
  const { label, dates, number, language, date } = useDashboardLabels()
  const mm = label('mm', 'мм')
  const historical = period.end < today
  const load = useCallback((signal: AbortSignal) => historical ? Promise.resolve(null)
    : api.waterForecast(field.id, signal, field.boundary_revision), [historical, field.id, field.boundary_revision])
  const forecast = useResource(load, historical ? undefined : 5 * 60 * 1000)
  const { reload } = forecast
  const saved = useJob(jobId)
  const history = matchingWaterResult(saved.job, context, field.boundary_revision)
  const historicalRun = explicitHistoricalRun(jobId, saved.job, history)
  const historyDays = historicalRun && history ? history.days : []
  const savedStart = historyDays[0]?.date
  const savedEnd = historyDays.at(-1)?.date
  const launch = useStartJob()
  const [refreshId, setRefreshId] = useState<string>()
  const { job, error } = useJob(refreshId)
  const refreshing = Boolean(refreshId && !error && (!job || ['queued', 'running', 'retrying'].includes(job.status)))
  useEffect(() => {
    if (job?.status === 'succeeded') reload()
  }, [job?.id, job?.status, reload])

  async function refresh() {
    const next = await launch.start('simulation', { field_id: field.id, season: Number(today.slice(0, 4)), scenario: 'baseline' },
      0, { source: 'forecast', analysis: 'water_forecast' }, field.boundary_revision)
    if (next) setRefreshId(next.id)
  }

  const assessment = droughtWindow(field, forecast.data, today, period)
  const { current, risks, complete, applicable, covered } = assessment
  const applicability = current?.applicability
  const display = waterReferenceDisplay(applicability, risks.length > 0, language)
  const temperature = applicability?.soil_temperature
    .filter(item => covered.some(day => item.date === day.date))
    .map(item => item.mean_c)
  const status = {
    missing: label('No current root-zone forecast for the recorded crop.', 'Немає актуального прогнозу для кореневого шару під зареєстрованою культурою.'),
    historical: label('No operational drought assessment for historical dates.', 'Для історичних дат оперативної оцінки посухи немає.'),
    expired: label('The stored forecast has expired.', 'Горизонт збереженого прогнозу минув.'),
    stale: label('The stored forecast is out of date.', 'Збережений прогноз застарів.'),
    invalid: label('The stored forecast cannot be used for this field and period.', 'Збережений прогноз непридатний для цього поля та періоду.'),
    incomplete: label('There is no root-zone forecast coverage for these dates.', 'Для цих дат немає прогнозу запасу в кореневому шарі.'),
  }
  const coverage = covered.length ? `${dates({ start: covered[0].date, end: covered.at(-1)!.date })} · ${covered.length}/${periodDays(assessment.period)} ${label('days covered', 'днів покрито')}` : null
  const days = historical ? historyDays.filter(day => day.date < today && contains(period, day.date))
    : covered.filter(day => contains(period, day.date))
  const threshold = historical ? history?.threshold_mm : current?.threshold_mm
  const metrics = threshold === undefined ? null : waterMetrics(days, threshold)
  const chartTitle = label('Root-zone water reserve', 'Запас води в кореневому шарі')
  const provenance = historical ? history?.provenance : undefined
  const source = historical
    ? label('Historical water balance model', 'Історична модель водного балансу')
    : current ? `${label('Operational model', 'Оперативна модель')} · ${current.provider}` : ''
  const span = days.length ? { start: days[0].date, end: days.at(-1)!.date } : null
  const reserve = reservePoints(days).map(item => historical ? { ...item, source: 'reanalysis' as const } : item)
  const reserveLabel = (item: DashboardSpan) => item.source ? source : label('No model result', 'Немає результату моделі')
  const waterUnit = label('m', 'м')
  const depth = current?.root_depth_m ?? provenance?.root_depth_m
  const canRefresh = launch.allowed && assessment.live && !forecast.error && supportsWaterReference(field.crop)
  const budget = cumulativeWaterBudget(days)

  return <div className="dashboard-water">
    <section className={`dashboard-warning${risks.length && applicable ? ' has-risk' : ''}`} aria-labelledby="drought-heading">
      <h2 id="drought-heading">{assessment.live ? label('Drought · next 30 days', 'Посуха · найближчі 30 днів') : label('Drought · selected period', 'Посуха · вибраний період')}</h2>
      <p className="dashboard-caption">{dates(assessment.period)} · UTC</p>
      <div role="status">
        {forecast.pending && !historical ? <p>{label('Loading water forecast…', 'Завантажуємо прогноз запасу води…')}</p>
          : forecast.error ? <p>{label('Water forecast temporarily unavailable.', 'Прогноз запасу води тимчасово недоступний.')}</p>
          : !covered.length ? <p>{status[assessment.reason]}</p>
          : <>
            {risks.length > 0 && <ul>{risks.map(risk => <li key={risk.start}>
              <strong>{applicable ? label('Drought risk', 'Ризик посухи') : label('Modeled reserve below reference threshold', 'Розрахунковий запас нижче довідкового порога')}</strong> · {dates(risk)}
            </li>)}</ul>}
            {complete && applicable && !risks.length && <p>{assessment.live
              ? label('No drought expected in the near term', 'Найближчим часом посуха не очікується')
              : label('No model drought signal in the selected period', 'Модель не виявила сигналу посухи у вибраному періоді')}</p>}
            {!complete && <p>{label('Incomplete forecast coverage.', 'Неповне покриття прогнозу.')} {coverage}</p>}
            {!applicable && <p>{label('Drought assessment limited by model applicability.', 'Оцінку посухи обмежує застосовність моделі.')}</p>}
          </>}
      </div>
    {current && covered.length > 0 && <details className="dashboard-details">
        <summary>{label('Model and threshold', 'Модель і поріг')}</summary>
        <p>{label('Signal when daily modeled reserve falls below', 'Сигнал, коли добовий розрахунковий запас падає нижче')} {number(current.threshold_mm)} {mm}.</p>
        <p role="note">{display.caveat} {display.limitation}</p>
        {applicability && temperature && temperature.length > 0 && <p>{label('Soil temperature · 0–7 cm', 'Температура ґрунту · 0–7 см')} · {
          [...new Set([Math.min(...temperature), Math.max(...temperature)])].map(value => number(value)).join(' – ')
        } °C · <code>{applicability.source_variable}</code></p>}
        <p>{current.physical_model_version} · <a href={current.source_url}>{current.provider}</a> · {date(current.initialized_at, true)} UTC</p>
      </details>}
      {canRefresh && <button type="button" className="button" disabled={launch.submitting || refreshing} onClick={() => void refresh()}>
        {label('Update water forecast', 'Оновити прогноз запасу води')}
      </button>}
      {forecast.error && <button type="button" className="button subtle" onClick={reload}>{label('Retry loading', 'Спробувати ще раз')}</button>}
      {refreshId && <JobStatus job={job} error={error} />}
      {launch.error && <ErrorBox message={launch.error} />}
    </section>
    {jobId && <section className="dashboard-balance">
      <h3>{label('Linked water balance', 'Водний баланс за посиланням')}</h3>
      {savedStart && savedEnd && historicalRun ? <>
        <p>{dates({ start: savedStart, end: savedEnd })} · {label('Historical model', 'Історична модель')}</p>
        <button className="button" type="button" onClick={() => choosePeriod({ start: savedStart, end: historyDays[Math.min(historyDays.length, 92) - 1].date })}>{label('View run dates', 'Показати дати розрахунку')}</button>
        <button className="button subtle" type="button" onClick={() => openAgent(historicalRun)}>{label('Ask agent about this run', 'Запитати агента про цей розрахунок')}</button>
      </> : saved.error || saved.job?.status === 'succeeded' ? <p>{label('The linked run is unavailable for this field and boundary.', 'Розрахунок за посиланням недоступний для цього поля й меж.')}</p>
        : <JobStatus job={saved.job} />}
    </section>}
    {span && metrics && threshold !== undefined && <div className="dashboard-charts">
      {(!historical || depth !== undefined) && <DashboardChart title={chartTitle} unit={mm} period={period} spans={withCoverage(period, reserve)}
        caption={depth === undefined ? undefined : `${label('Root depth', 'Глибина коріння')} ${number(depth)} ${waterUnit}`}
        threshold={threshold} describe={reserveLabel} details={<p>{label('Dashed line · reference threshold', 'Пунктир · довідковий поріг')} {number(threshold)} {mm}</p>} />}
      <section className="dashboard-balance">
        <h3>{label('Water balance', 'Водний баланс')} <small>· {mm}</small></h3>
        <p className="dashboard-caption">{dates(span)} · {source}</p>
        {days.length < periodDays(period) && <p className="dashboard-caption">{days.length}/{periodDays(period)} {label('days covered', 'днів покрито')}</p>}
        {budget.kind === 'available' ? <dl>{([
          [label('Inputs · rain + irrigation', 'Надходження · опади + полив'), budget.totals.incoming],
          [label('Losses · water use + drainage', 'Втрати · водоспоживання + дренаж'), budget.totals.outgoing],
          [label('Net balance', 'Чистий баланс'), budget.totals.change],
        ] as const).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{number(value)} {mm}</dd></div>)}</dl>
          : <p>{label('The available days do not form a complete water budget.', 'Доступні доби не утворюють повного водного балансу.')}</p>}
      </section>
    </div>}
    {!span && (!forecast.pending || historical) && <p className="dashboard-caption">{chartTitle} · {label('No estimate for these dates', 'Немає оцінки для цих дат')}</p>}
  </div>
}

function withCoverage(period: Period, spans: DashboardSpan[]) {
  const gap = (start: string, end: string): DashboardSpan => ({ start, end, source: null, reason: null, points: [] })
  let start = period.start
  const result: DashboardSpan[] = []
  for (const span of spans) {
    if (start < span.start) result.push(gap(start, shiftDay(span.start, -1)))
    result.push(span)
    start = shiftDay(span.end, 1)
  }
  if (start <= period.end) result.push(gap(start, period.end))
  return result
}
