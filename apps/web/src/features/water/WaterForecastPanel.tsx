import { ArrowPathIcon, CheckCircleIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { ContextHelp } from '../../shared/ContextHelp'
import { useLocale } from '../../shared/locale'
import { useJob, useStartJob } from '../../shared/jobs'
import { JobStatus } from '../../shared/JobStatus'
import { Empty, ErrorBox, Loading } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { selectWaterForecast } from './waterForecast'
import { getCurrentWaterOutlook } from './waterOutlook'
import { WaterForecastChart } from './WaterForecastChart'
import { WaterBudgetPanel } from './WaterBudgetPanel'
import { waterHelp } from './waterHelp'

const refreshIntervalMs = 5 * 60 * 1000

export function WaterForecastPanel({ fieldId, boundaryRevision, forecastDay }: {
  fieldId: string
  boundaryRevision: number
  forecastDay?: string
}) {
  const { t, number, crop, date, shortDate, language } = useLocale()
  const initialHelp = waterHelp[language].initialReserve
  const heading = useRef<HTMLHeadingElement>(null)
  const load = useCallback((signal: AbortSignal) => api.waterForecast(fieldId, signal), [fieldId, boundaryRevision])
  const saved = useResource(load, refreshIntervalMs)
  const { reload } = saved
  const launch = useStartJob()
  const [jobId, setJobId] = useState<string>()
  const { job, error } = useJob(jobId)
  const forecast = selectWaterForecast(fieldId, (job?.boundary_revision ?? 0) === boundaryRevision ? job : undefined, saved.data)
  const today = new Date().toISOString().slice(0, 10)
  const outlook = getCurrentWaterOutlook(forecast, fieldId, today)
  const days = outlook.kind === 'unavailable' ? [] : outlook.days
  const hasModelEstimate = forecast === saved.data && forecast?.method === 'ml'
    && days.some(({ root_zone_estimate_mm }) => root_zone_estimate_mm != null)
  const pending = Boolean(jobId && !error && (!job || job.status === 'queued' || job.status === 'running' || job.status === 'retrying'))
  const sourceSummary = forecast && `${t('FAO-56 water balance')} · ${forecast.provider} · ${number(forecast.grid_resolution_km)} ${t('km')} · UTC`

  useEffect(() => {
    if (job?.status === 'succeeded') reload()
  }, [job?.id, job?.status, reload])

  useEffect(() => {
    if (forecastDay) heading.current?.focus({ preventScroll: true })
  }, [forecastDay, fieldId])

  async function updateForecast() {
    const season = Number(today.slice(0, 4))
    const next = await launch.start('simulation', { field_id: fieldId, season, scenario: 'baseline' }, 0,
      { source: 'forecast', analysis: 'water_forecast' })
    if (next) setJobId(next.id)
  }

  return (
    <section className="water-forecast-panel" aria-labelledby="root-zone-forecast-heading">
      <div className="section-heading">
        <div>
          <h2 id="root-zone-forecast-heading" ref={heading} tabIndex={-1}>{t('Will there be enough water for the coming days?')}</h2>
          <p>{t('Root-zone forecast')}{outlook.kind !== 'unavailable' && <>{' · '}
            <time dateTime={outlook.firstDay.date}>{shortDate(outlook.firstDay.date)}</time>
            {outlook.firstDay.date !== outlook.lastDay.date && <>{'–'}<time dateTime={outlook.lastDay.date}>{shortDate(outlook.lastDay.date)}</time></>}
          </>}</p>
        </div>
        <button className="button primary water-refresh" type="button" aria-label={t('Update forecast')} onClick={() => void updateForecast()}
          disabled={!launch.allowed || launch.submitting || pending}>
          <ArrowPathIcon aria-hidden="true" /><span>{launch.submitting ? t('Submitting…') : t('Refresh')}</span>
        </button>
      </div>
      {forecast && outlook.kind !== 'unavailable' && (
        <>
          <div className="water-outlook" data-status={outlook.kind}>
            <div className="water-outlook-status">
              {outlook.kind === 'shortage' ? <ExclamationTriangleIcon aria-hidden="true" /> : <CheckCircleIcon aria-hidden="true" />}
              <div>
                <strong>{outlook.kind === 'shortage'
                  ? <>{t('Water shortage risk from')}{' '}<time dateTime={outlook.firstShortage.date}>{shortDate(outlook.firstShortage.date)}</time></>
                  : t('No water shortage expected')}</strong>
                <p>{t('Stress threshold')} — {number(outlook.thresholdMm)} {t('mm')}</p>
              </div>
            </div>
          </div>
          {forecast.days.some(({ date }) => date < today) && (
            <p className="analysis-day-source" role="status">{t('Showing the remaining days from an earlier run.')}</p>
          )}
          {forecastDay && !days.some(({ date }) => date === forecastDay) && (
            <p className="analysis-day-source" role="status">{t('The linked day is no longer in the current forecast.')}</p>
          )}
          <WaterForecastChart key={forecastDay} initialDate={forecastDay ?? (outlook.kind === 'shortage' ? outlook.firstShortage.date : undefined)}
            threshold={outlook.thresholdMm} days={days} showModelEstimate={hasModelEstimate}
            rootDepthMetres={forecast.root_depth_m} provider={forecast.provider} />
          <footer className="water-forecast-footer">
            <p className="water-source-summary">{sourceSummary}</p>
            <details className="analysis-details water-source-details">
              <summary>{t('Sources and assumptions')}</summary>
              <div className="water-source-content">
                <p className="water-source-mobile-summary">{sourceSummary}</p>
                <p>{t('Initialized')}{' '}<time dateTime={forecast.initialized_at}>{date(forecast.initialized_at, true)} UTC</time>{' · '}
                  <a href={forecast.source_url} target="_blank" rel="noreferrer">{t('Weather forecast source')}</a></p>
                {hasModelEstimate && <p>{t('Validated ML estimate')} · {forecast.model_version}</p>}
                <dl className="data-strip analysis-source-grid">
                  <div><dt>{t('Root depth')}</dt><dd>{number(forecast.root_depth_m, 2)} {t('m')}</dd></div>
                  <div><dt>{t('Initial reserve')}{' '}
                    <ContextHelp label={initialHelp.title}><p>{initialHelp.description}</p></ContextHelp>
                  </dt><dd>{number(forecast.initial_reserve_mm)} {t('mm')}</dd></div>
                  <div><dt>{t('Soil capacity')}</dt><dd>{number(forecast.capacity_mm)} {t('mm')}</dd></div>
                  <div><dt>{t('Stress threshold')}</dt><dd>{number(forecast.threshold_mm)} {t('mm')}</dd></div>
                </dl>
                <p>
                  {crop(forecast.crop)} · Kc {number(forecast.crop_coefficient, 2)} · {t('Reference field capacity')}{' '}
                  {number(forecast.field_capacity * 100)}% · {t('Wilting point')} {number(forecast.wilting_point * 100)}%
                  {' · '}{forecast.physical_model_version}
                </p>
                <ul className="analysis-limitations">
                  {forecast.limitations.map((limitation) => <li key={limitation}>{t(limitation)}</li>)}
                </ul>
              </div>
            </details>
          </footer>
          <WaterBudgetPanel days={days} mode="forecast" />
        </>
      )}
      {outlook.kind === 'unavailable' && ['expired', 'stale'].includes(outlook.reason) && (
        <Empty>{t('The last saved forecast has expired. Update it to see the upcoming days.')}</Empty>
      )}
      {outlook.kind === 'unavailable' && outlook.reason === 'invalid' && (
        <Empty>{t('The saved forecast could not be read. Update it to try again.')}</Empty>
      )}
      {outlook.kind === 'unavailable' && ['missing', 'incomplete'].includes(outlook.reason) && (
        saved.pending ? <Loading text={t('Loading root-zone forecast…')} />
          : !saved.error && !pending && <Empty>{t('Update the forecast to calculate the upcoming water balance.')}</Empty>
      )}
      {saved.error && <ErrorBox message={saved.error} retry={saved.reload} />}
      {launch.error && <ErrorBox message={launch.error} />}
      {jobId && <JobStatus job={job} error={error} />}
    </section>
  )
}
