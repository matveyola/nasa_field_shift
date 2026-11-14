import { useCallback, useState } from 'react'
import { api } from '../../api/client'
import type { FieldContext, Scenario, WaterResult } from '../../api/contracts'
import { matchingWaterResult } from '../../shared/analysis'
import { ComparisonBars } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'
import { JobStatus } from '../../shared/JobStatus'
import { useJob, useStartJob } from '../../shared/jobs'
import { ErrorBox, Loading, ScenarioSelect } from '../../shared/ui'
import { useResource } from '../../shared/useResource'

export function ScenarioComparison({ context, baseline, boundaryRevision }: {
  context: FieldContext; baseline: WaterResult; boundaryRevision: number
}) {
  const { t } = useLocale()
  const [other, setOther] = useState<Scenario>(context.scenario === 'less_rain' ? 'baseline' : 'less_rain')
  const [jobId, setJobId] = useState<string>()
  const { job, error } = useJob(jobId)
  const create = useStartJob()
  const { field_id, season } = context
  const load = useCallback((signal: AbortSignal) => baseline.demo
    ? api.water({ field_id, season, scenario: other }, signal)
    : Promise.resolve(undefined), [baseline.demo, field_id, season, other])
  const preview = useResource(load)
  const alternative = baseline.demo ? preview.data : matchingWaterResult(job, { ...context, scenario: other }, boundaryRevision)
  const provenance = baseline.provenance
  const active = Boolean(jobId && (!job ? !error : job.status !== 'succeeded' && job.status !== 'failed'))

  async function run() {
    if (!provenance) return
    const next = await create.start(
      'simulation',
      { ...context, scenario: other },
      baseline.days[0]?.irrigation_mm ?? 0,
      {
        source: 'historical',
        start_date: provenance.start_date,
        end_date: provenance.end_date,
        initial_reserve_mm: provenance.initial_reserve_mm,
        flowering_start: baseline.flowering_start ?? undefined,
        flowering_end: baseline.flowering_end ?? undefined,
      },
    )
    if (next) setJobId(next.id)
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <div><h2>{t('Scenario comparison')}</h2>
          <p>{baseline.demo
            ? t('Both scenarios use the demo weather series.')
            : t('The alternative uses the same dates, irrigation, and initial reserve.')}</p>
        </div>
        <div className="toolbar">
          <ScenarioSelect value={other} onChange={(scenario) => { setOther(scenario); setJobId(undefined) }} />
          {!baseline.demo && (
            <button className="button primary" type="button" disabled={!create.allowed || !provenance || active || create.submitting || other === context.scenario}
              onClick={() => void run()}>{t('Run comparison')}</button>
          )}
        </div>
      </div>
      {create.error && <ErrorBox message={create.error} />}
      {jobId && <JobStatus job={job} error={error} />}
      {baseline.demo && preview.pending && <Loading />}
      {preview.error && <ErrorBox message={preview.error} retry={preview.reload} />}
      {alternative && (

        <ComparisonBars groups={[
          { label: t('Stress days'), unit: t('d'), values: [
            { label: t('Current'), value: baseline.stress_days }, { label: t('Alternative'), value: alternative.stress_days },
          ] },
          { label: t('Minimum reserve'), unit: t('mm'), values: [
            { label: t('Current'), value: baseline.min_reserve_mm }, { label: t('Alternative'), value: alternative.min_reserve_mm },
          ] },
        ]} />
      )}
    </section>
  )
}
