import { useLocale } from '../../shared/locale'
import { useCallback, useEffect, useState } from 'react'
import { AdjustmentsHorizontalIcon, ArrowPathIcon, CalendarDaysIcon, ChartBarIcon, ChevronLeftIcon, ChevronRightIcon, ExclamationTriangleIcon, SparklesIcon } from '@heroicons/react/24/outline'
import { api } from '../../api/client'
import type { Field, FieldContext, JobOptions, Scenario, WaterResult } from '../../api/contracts'
import { isWaterResult, matchingWaterResult } from '../../shared/analysis'
import { explicitHistoricalRun } from '../assistant/assistantRun'
import { useJob, useStartJob } from '../../shared/jobs'
import { JobStatus } from '../../shared/JobStatus'
import { Empty, ErrorBox, Loading, PageHeading, ScenarioSelect } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { CropIcon } from '../../shared/CropIcon'
import { ScenarioComparison } from './ScenarioComparison'
import { WaterChart } from './WaterChart'
import { WaterForecastPanel } from './WaterForecastPanel'
import { WaterProvenance } from './WaterProvenance'
import { WaterBudgetPanel } from './WaterBudgetPanel'
import './water.css'

type WaterView = 'forecast' | 'scenarios'

type Props = {
  fields: Field[]
  field: Field
  context: FieldContext
  jobId?: string
  forecastDay?: string
  irrigationAvailable: boolean
  chooseField: (fieldId: string) => void
  chooseScenario: (scenario: Scenario) => void
  showJob: (id: string, scenario?: Scenario) => void
  openAgent: (jobId?: string) => void
  openFields: () => void
}

export function WaterPage(props: Props) {
  const { t, number, crop } = useLocale()
  const { fields, field, context: { field_id, season }, forecastDay, jobId } = props
  const routeIntent = forecastDay ? 'day:' + forecastDay : jobId ? 'job:' + jobId : undefined
  const routeView = jobId && !forecastDay ? 'scenarios' : 'forecast'
  const [selection, setSelection] = useState<{ intent?: string; view: WaterView; visitedScenarios: boolean }>({
    intent: routeIntent, view: routeView, visitedScenarios: routeView === 'scenarios',
  })
  const view = routeIntent !== undefined && routeIntent !== selection.intent ? routeView : selection.view
  if (routeIntent !== selection.intent) {
    setSelection({ intent: routeIntent, view, visitedScenarios: selection.visitedScenarios || view === 'scenarios' })
  }

  function chooseView(next: WaterView) {
    setSelection({ intent: routeIntent, view: next, visitedScenarios: selection.visitedScenarios || next === 'scenarios' })
  }

  function stepField(direction: number) {
    const index = fields.findIndex(({ id }) => id === field_id)
    props.chooseField(fields[(index + direction + fields.length) % fields.length].id)
  }

  return (
    <div className="page-body water-page">
      <PageHeading title="Water" season={season} dataLabel={null} />
      <div className="water-workspace">
        <div className="water-workspace-controls">
          <button className="button water-back" type="button" onClick={props.openFields}>
            <ChevronLeftIcon aria-hidden="true" />{t('Back to map')}
          </button>
          <div className="water-field-switcher">
            <button className="icon-button" type="button" aria-label={t('Previous field')}
              disabled={fields.length < 2} onClick={() => stepField(-1)}><ChevronLeftIcon aria-hidden="true" /></button>
            <strong className="water-field-name" aria-live="polite">{field.name}</strong>
            <button className="icon-button water-field-next" type="button" aria-label={t('Next field')}
              disabled={fields.length < 2} onClick={() => stepField(1)}><ChevronRightIcon aria-hidden="true" /></button>
            <span className="crop-label water-field-crop"><CropIcon crop={field.crop} />{crop(field.crop)} · {number(field.area_ha)} {t('ha')}</span>
          </div>
          <div className="water-view-toggle" role="group" aria-label={t('Water view')}>
            <button type="button" aria-pressed={view === 'forecast'} aria-controls="water-forecast-view"
              onClick={() => chooseView('forecast')}>
              <CalendarDaysIcon aria-hidden="true" />{t('Forecast')}
            </button>
            <button type="button" aria-pressed={view === 'scenarios'} aria-controls="water-scenarios-view"
              onClick={() => chooseView('scenarios')}>
              <AdjustmentsHorizontalIcon aria-hidden="true" />{t('Scenarios')}
            </button>
          </div>
        </div>
        <div id="water-forecast-view" className="water-view-content" hidden={view !== 'forecast'}>
          <WaterForecastPanel key={field_id + ':' + field.boundary_revision} fieldId={field_id}
            boundaryRevision={field.boundary_revision} forecastDay={forecastDay} />
        </div>
        <div id="water-scenarios-view" className="page-stack water-view-content" hidden={view !== 'scenarios'}>
          {selection.visitedScenarios && <WaterScenarios key={field_id + ':' + field.boundary_revision} {...props} />}
        </div>
      </div>
    </div>
  )
}

function WaterScenarios(props: Props) {
  const { t, number, shortDate } = useLocale()
  const { field, context: { field_id, season, scenario }, jobId } = props
  const [source, setSource] = useState<NonNullable<WaterResult['source']>>('historical')
  const loadWater = useCallback(
    (signal: AbortSignal) => source === 'demo' ? api.water({ field_id, season, scenario }, signal) : Promise.resolve(undefined),
    [field_id, season, scenario, source],
  )
  const water = useResource(loadWater)
  const loadRuns = useCallback((signal: AbortSignal) => api.jobs(field_id, signal), [field_id])
  const runs = useResource(loadRuns)
  const { job, error } = useJob(jobId)
  const create = useStartJob()
  const [selectedDate, setSelectedDate] = useState<string>()
  const [irrigation, setIrrigation] = useState(0)
  const [comparison, setComparison] = useState(false)
  const [startDate, setStartDate] = useState(season + '-08-01')
  const [endDate, setEndDate] = useState(season + '-08-10')
  const [initialReserve, setInitialReserve] = useState('')
  const [floweringStart, setFloweringStart] = useState('')
  const [floweringEnd, setFloweringEnd] = useState('')

  const history = runs.data?.filter(({ kind, analysis, boundary_revision }) =>
    (boundary_revision ?? 0) === field.boundary_revision
    && kind === 'simulation' && analysis !== 'rotation_comparison' && analysis !== 'water_forecast') ?? []
  const archivedRun = job && (job.boundary_revision ?? 0) !== field.boundary_revision
  const latest = !jobId && source === 'historical'
    ? history.find((item) => matchingWaterResult(item, props.context, field.boundary_revision)?.source === 'historical')
    : undefined
  const completed = matchingWaterResult(job ?? latest, props.context, field.boundary_revision)
  const preview = water.data
  const result = completed ?? (source === 'demo' && preview?.field_id === field_id
    && preview.season === season && preview.scenario === scenario ? preview : undefined)
  const active = Boolean(jobId && (!job ? !error : job.status !== 'succeeded' && job.status !== 'failed'))
  // Only the run the farmer selected, never the latest one shown by default, goes to the assistant.
  const askedRun = explicitHistoricalRun(jobId, job, completed)
  const day = result?.days.find(({ date }) => date === selectedDate) ?? result?.days.find(({ date }) => date === result.first_stress_date) ?? result?.days[0]
  const { reload: reloadRuns } = runs

  useEffect(() => {
    if (job?.status === 'succeeded' || job?.status === 'failed') reloadRuns()
  }, [job?.status, reloadRuns])

  async function run() {
    const options: JobOptions = source === 'historical'
      ? {
          source, start_date: startDate, end_date: endDate,
          initial_reserve_mm: initialReserve === '' ? undefined : Number(initialReserve),
          flowering_start: floweringStart || undefined, flowering_end: floweringEnd || undefined,
        }
      : { source }
    const next = await create.start(
      'simulation', { field_id, season, scenario }, props.irrigationAvailable ? irrigation : 0, options,
    )
    if (next) {
      props.showJob(next.id)
      runs.reload()
    }
  }

  return (
    <>
      <section className="panel water-simulation-form" aria-labelledby="water-simulation-heading">
        <div className="section-heading">
          <div>
            <h2 id="water-simulation-heading">{t('Scenario simulations')}</h2>
            <p>{t('Choose a scenario and period to see how the field’s water balance changes.')}</p>
          </div>
          {history.length > 0 && (
            <label className="control"><span>{t('Saved runs')}</span>
              <select value={jobId ?? latest?.id ?? ''} onChange={(event) => {
                const saved = history.find(({ id }) => id === event.target.value)
                if (saved) props.showJob(saved.id, isWaterResult(saved.result) ? saved.result.scenario : undefined)
              }}>
                <option value="">{t('Choose a run')}</option>
                {history.map((item) => <option key={item.id} value={item.id}>{shortDate(item.created_at)} · {t(item.status)} · {item.id.slice(0, 8)}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="toolbar water-simulation-inputs">
          <ScenarioSelect value={scenario} onChange={props.chooseScenario} />
          <label className="control"><span>{t('Data')}</span>
            <select value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
              <option value="historical">{t('ERA5 historical')}</option>
              <option value="demo">{t('Demo')}</option>
            </select>
          </label>
          {source === 'historical' && (
            <>
              <label className="control"><span>{t('From')}</span><input type="date" value={startDate}
                onChange={(event) => setStartDate(event.target.value)} /></label>
              <label className="control"><span>{t('To')}</span><input type="date" value={endDate} min={startDate}
                onChange={(event) => setEndDate(event.target.value)} /></label>
            </>
          )}
          {props.irrigationAvailable && (
            <label className="control"><span>{t('Irrigation (mm)')}</span>
              <input aria-label={t('Irrigation in millimeters')} type="number" min="0" max="100" step=".1" value={irrigation}
                onChange={(event) => setIrrigation(event.target.valueAsNumber || 0)} />
            </label>
          )}
        </div>
        {source === 'historical' && (
          <details className="analysis-options">
            <summary>{t('Model inputs')}</summary>
            <div className="toolbar analysis-period">
              <label className="control"><span>{t('Initial reserve (mm)')}</span>
                <input type="number" min="0" step="any" value={initialReserve} placeholder="ERA5"
                  onChange={(event) => setInitialReserve(event.target.value)} />
              </label>
              <label className="control"><span>{t('Flowering starts')}</span><input type="date" value={floweringStart}
                onChange={(event) => setFloweringStart(event.target.value)} /></label>
              <label className="control"><span>{t('Flowering ends')}</span><input type="date" value={floweringEnd} min={floweringStart}
                onChange={(event) => setFloweringEnd(event.target.value)} /></label>
            </div>
          </details>
        )}
        <div className="water-simulation-actions">
          {!props.irrigationAvailable && <span className="chip">{t('No irrigation')}</span>}
          <button className="button primary" type="button" disabled={!create.allowed || create.submitting || active || irrigation < 0 || irrigation > 100
            || (source === 'historical' && (!startDate || !endDate || endDate < startDate
              || Boolean(floweringStart) !== Boolean(floweringEnd) || floweringEnd < floweringStart
              || (initialReserve !== '' && !(Number(initialReserve) >= 0))))} onClick={() => void run()}>
            <ArrowPathIcon aria-hidden="true" />{create.submitting ? t('Submitting…') : t('Run simulation')}
          </button>
        </div>
      </section>
      {create.error && <ErrorBox message={create.error} />}
      {jobId && <JobStatus job={job} error={error} />}
      {archivedRun && <Empty>{t('This run used an earlier field boundary. Run the simulation again.')}</Empty>}
      {!archivedRun && job?.status === 'succeeded' && isWaterResult(job.result) && result !== job.result && (
        <Empty>{t('This run belongs to another field, season, or scenario. Select the matching context to view it.')}</Empty>
      )}
      {source === 'demo' && water.pending && !result && <Loading text={t('Loading scenario…')} />}
      {source === 'demo' && water.error && !result && <ErrorBox message={water.error} retry={water.reload} />}
      {source === 'historical' && !result && !jobId && !runs.pending && (
        <Empty>{t('Run the water balance to view this field’s historical scenario.')}</Empty>
      )}
      {result && <WaterMetrics result={result} />}
      {result && day && (
        <>
          <section className="panel">
            <div className="section-heading">
              <div><h2>{t('Root-zone water reserve')}</h2>
                <p>{result.demo ? (completed ? t('Demo run') : t('Demo preview')) : t('Historical scenario')} · {t('mm')}</p></div>
              <button className="button subtle" type="button" aria-expanded={comparison}
                onClick={() => setComparison(!comparison)}>{t('Compare scenario')}</button>
            </div>
            <WaterChart result={result} selected={day.date} onSelect={setSelectedDate} />
          </section>
          <section className="panel">
            <div className="section-heading">
              <h2>{t('Daily balance')}</h2>
              <label className="control"><span>{t('Day')}</span><select value={day.date} onChange={(event) => setSelectedDate(event.target.value)}>
                {result.days.map(({ date }) => <option key={date} value={date}>{shortDate(date)}</option>)}
              </select></label>
            </div>
            <dl className="data-strip">
              <div><dt>{t('Opening reserve')}</dt><dd>{number(day.opening_mm)} {t('mm')}</dd></div>
              <div><dt>{t('Rain')}</dt><dd>{number(day.rain_mm)} {t('mm')}</dd></div>
              <div><dt>{t('Irrigation')}</dt><dd>{number(day.irrigation_mm)} {t('mm')}</dd></div>
              <div><dt>{t('Water use')}</dt><dd>{number(day.water_use_mm)} {t('mm')}</dd></div>
              <div><dt>{t('Drainage')}</dt><dd>{number(day.drainage_mm)} {t('mm')}</dd></div>
              <div><dt>{t('Closing reserve')}</dt><dd>{number(day.reserve_mm)} {t('mm')}</dd></div>
            </dl>
            {day.source_rain_mm != null && day.reference_et0_mm != null && (
              <p className="analysis-day-source">{t('ERA5 inputs · Rain')} {number(day.source_rain_mm)} {t('mm')} · ET₀ {number(day.reference_et0_mm)} {t('mm')}</p>
            )}
          </section>
        </>
      )}
      {result?.provenance && <WaterProvenance data={result.provenance} />}
      {result && !result.demo && result.source === 'historical' && <section className="panel">
        <WaterBudgetPanel days={result.days} mode="historical" />
      </section>}
      {result && comparison && <ScenarioComparison key={jobId ?? 'preview'} context={props.context} baseline={result}
        boundaryRevision={field.boundary_revision} />}
      <div className="toolbar">
        <button className="button" type="button" onClick={() => props.openAgent(askedRun)}>
          <SparklesIcon aria-hidden="true" />{askedRun ? t('Ask agent about this run') : t('Ask agent')}
        </button>
      </div>
    </>
  )
}

function WaterMetrics({ result }: { result: WaterResult }) {
  const { t, shortDate, number } = useLocale()
  return (
    <div className="metric-grid">
      <div className="metric"><CalendarDaysIcon aria-hidden="true" /><div><span>{t('Stress starts')}</span><strong>{result.first_stress_date ? shortDate(result.first_stress_date) : t('None')}</strong></div></div>
      <div className="metric"><ChartBarIcon aria-hidden="true" /><div><span>{t('Minimum reserve')}</span><strong>{number(result.min_reserve_mm)} {t('mm')}</strong></div></div>
      <div className="metric"><ExclamationTriangleIcon aria-hidden="true" /><div><span>{t('Stress days')}</span><strong>{result.stress_days}</strong></div></div>
    </div>
  )
}
