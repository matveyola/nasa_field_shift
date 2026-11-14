import { ArrowPathIcon } from '@heroicons/react/24/outline'
import { useCallback, useState } from 'react'
import { api } from '../../api/client'
import { chartColors, ForecastTrend } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'
import { useJob, useStartJob } from '../../shared/jobs'
import { JobStatus } from '../../shared/JobStatus'
import { ErrorBox } from '../../shared/ui'
import { useResource } from '../../shared/useResource'

export function useMoistureForecast(fieldId: string | undefined, boundaryRevision: number) {
  const load = useCallback(async (signal: AbortSignal) => fieldId
    ? { revision: boundaryRevision, forecast: await api.moistureForecast(fieldId, signal) } : undefined, [fieldId, boundaryRevision])
  const saved = useResource(load, fieldId ? 5 * 60 * 1000 : undefined)
  const launch = useStartJob()
  const [request, setRequest] = useState<{ fieldId: string; revision: number; jobId: string }>()
  const jobId = request?.fieldId === fieldId && request?.revision === boundaryRevision ? request.jobId : undefined
  const { job, error } = useJob(jobId)
  const active = job?.result && 'product' in job.result && job.result.product === 'moisture_forecast'
    && job.result.field_id === fieldId && (job.boundary_revision ?? 0) === boundaryRevision ? job.result : null
  const cached = saved.data?.revision === boundaryRevision ? saved.data.forecast : undefined
  const stored = cached?.field_id === fieldId ? cached : null
  const activeIsNewer = active && (!stored || active.initialized_at > stored.initialized_at ||
    (active.initialized_at === stored.initialized_at && active.fetched_at > stored.fetched_at))
  const forecast = activeIsNewer ? active : stored
  const pending = jobId && !error && job?.status !== 'succeeded' && job?.status !== 'failed'
  const today = new Date().toISOString().slice(0, 10)
  const ageDays = forecast ? (Date.parse(today) - Date.parse(forecast.initialized_at)) / 86_400_000 : 0
  const days = forecast?.days.filter((day) => day.date >= today) ?? []

  async function updateForecast() {
    if (!fieldId) return
    const season = Number(today.slice(0, 4))
    const next = await launch.start('geo_ml', { field_id: fieldId, season, scenario: 'baseline' }, 0, { source: 'forecast' })
    if (next) setRequest({ fieldId, revision: boundaryRevision, jobId: next.id })
  }

  return { forecast, saved, today, days, ageDays, updateForecast, launch, pending, jobId, job, error }
}

export function MoistureForecastPanel({ state }: { state: ReturnType<typeof useMoistureForecast> }) {
  const { t, date } = useLocale()
  const { forecast, saved, days, ageDays, updateForecast, launch, pending, jobId, job, error } = state

  return (
    <section className="panel forecast-panel" aria-labelledby="soil-forecast-heading">
      <div className="section-heading">
        <div><h2 id="soil-forecast-heading">{t('Soil moisture forecast')}</h2>
          <p>{t('Surface moisture for today and the next four UTC days.')}</p></div>
        <button type="button" className="button subtle" onClick={() => void updateForecast()}
          disabled={launch.submitting || Boolean(pending)}>
          <ArrowPathIcon aria-hidden="true" />{t('Update forecast')}
        </button>
      </div>
      {forecast && ageDays > 1 && <p role="status" className="moisture-stale">{t('A newer forecast is pending.')}</p>}
      {forecast && !days.length && <p role="status">{t('The last saved run has expired.')}</p>}
      {!forecast && saved.pending && <p role="status">{t('Loading forecast…')}</p>}
      {!forecast && !saved.pending && !saved.error && <p role="status">{t('The next forecast will appear after the worker completes.')}</p>}
      {days.length > 0 && forecast && (
        <ForecastTrend unit={forecast.unit} series={[
          { label: t('Surface moisture'), color: chartColors[0] },
          ...(forecast.model_version ? [{ label: 'IFS 0–7 ' + t('cm'), color: chartColors[1] }] : []),
        ]} days={days.map((day) => ({
          date: day.date,
          values: [day.value, ...(forecast.model_version ? [day.ifs_value] : [])],
          rain: day.precipitation_mm,
          et0: day.et0_mm,
          interval: day.interval ? [day.interval.lower, day.interval.upper] : undefined,
          detail: day.interval ? 'ML 5 ' + t('cm') : 'IFS 0–7 ' + t('cm'),
        }))} />
      )}
      {forecast && (
        <p className="forecast-meta">
          {forecast.provider} · {t('Grid spacing')} {forecast.resolution_km} {t('km')} · {t('Initialized')}{' '}
          <time dateTime={forecast.initialized_at}>{date(forecast.initialized_at)}</time> {t('at 00:00 UTC')}
          {forecast.model_version && <> · {t('Model')} {forecast.model_version}</>}
          {' '}· <a href={forecast.source_url} target="_blank" rel="noreferrer">{t('Source')}</a>
        </p>
      )}
      {saved.error && <ErrorBox message={saved.error} retry={saved.reload} />}
      {launch.error && <ErrorBox message={launch.error} />}
      {jobId && <JobStatus job={job} error={error} />}
    </section>
  )
}
