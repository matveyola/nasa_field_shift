import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowPathIcon, ArrowRightIcon, CheckCircleIcon, PencilSquareIcon, PlusIcon } from '@heroicons/react/24/outline'
import { api } from '../../api/client'
import type { Field, FieldContext, HistoricalRotationComparison, Job, Rotations, Scenario } from '../../api/contracts'
import { onboardingApi } from '../../api/onboarding'
import { CropIcon } from '../../shared/CropIcon'
import { FieldWorkspaceBar } from '../../shared/FieldWorkspaceBar'
import { cropInput, useLocale } from '../../shared/locale'
import { useJob, useStartJob } from '../../shared/jobs'
import { JobStatus } from '../../shared/JobStatus'
import { Empty, ErrorBox, Loading, PageHeading, ScenarioSelect } from '../../shared/ui'
import { errorText, useResource } from '../../shared/useResource'
import { RotationYieldPanel } from './RotationYieldPanel'
import { RotationComparison } from './RotationComparison'
import { FarmRotationPlanner } from './FarmRotationPlanner'
import { useAccess } from '../access/AccessProvider'
import { OnboardingPlan } from '../onboarding/OnboardingPlan'
import './rotations.css'

type Props = {
  field: Field
  fields: Field[]
  context: FieldContext
  irrigationAvailable: boolean
  chooseScenario: (scenario: Scenario) => void
  openWater: () => void
  chooseField: (id: string) => void
  openFields: () => void
  onAddField?: () => void
}

export function RotationsPage({ field, fields, context: { field_id, season, scenario }, irrigationAvailable, chooseScenario, openWater, chooseField, openFields, onAddField }: Props) {
  const { t, crop, date } = useLocale()
  const { canEdit } = useAccess()
  const load = useCallback((signal: AbortSignal) => api.rotations({ field_id, season, scenario }, signal), [field_id, season, scenario])
  const rotations = useResource(load)
  const loadSetup = useCallback((signal: AbortSignal) => onboardingApi.field(field_id, signal), [field_id])
  const fieldSetup = useResource(loadSetup)
  const [selectedId, setSelectedId] = useState<string>()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()
  const [jobId, setJobId] = useState<string>()
  const editButton = useRef<HTMLButtonElement>(null)
  const saveNotice = useRef<HTMLParagraphElement>(null)
  const loadJobs = useCallback((signal: AbortSignal) => api.jobs(field_id, signal), [field_id])
  const jobs = useResource(loadJobs)
  const current = useJob(jobId)
  const create = useStartJob()
  const data = rotations.data
  const context = { field_id, season, scenario }
  const comparison = (job?: Job) => (job?.boundary_revision ?? 0) === field.boundary_revision
    ? matchComparison(job, context, data) : undefined
  const result = comparison(current.job) ?? jobs.data?.map(comparison).find(Boolean)
  const active = Boolean(jobId && (!current.job ? !current.error : current.job.status === 'queued'
    || current.job.status === 'running' || current.job.status === 'retrying'))
  const selected = data?.plans.find((plan) => plan.id === selectedId)
    ?? data?.plans.find((plan) => plan.id === data.main_plan_id) ?? data?.plans[0]
  const years = Array.from(new Set(data?.plans.flatMap(({ crops }) => crops.map(({ year }) => year)))).sort()
  const measures = [
    { label: 'Stress days', property: 'stress_days' },
    { label: 'Longest episode', property: 'longest_episode' },
    { label: 'Days in sensitive phase', property: 'sensitive_days' },
  ] as const

  useEffect(() => {
    if (notice) saveNotice.current?.focus()
  }, [notice])

  async function runHistoricalComparison() {
    if (saving || create.submitting || active) return
    const next = await create.start('simulation', context, 0, {
      source: 'historical', analysis: 'rotation_comparison',
    })
    if (next) setJobId(next.id)
  }

  async function markMain(planId: string) {
    if (saving) return
    setError(undefined)
    setNotice(undefined)
    setSaving(true)
    try {
      await api.setMainPlan({ field_id, season, plan_id: planId })
      setNotice('Main plan updated.')
      rotations.reload()
    }
    catch (failure) { setError(errorText(failure)) }
    finally { setSaving(false) }
  }

  async function editPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || saving) return
    const values = new FormData(event.currentTarget)
    const crops = selected.crops.map(({ year }) => cropInput(String(values.get('crop-' + year) ?? '').trim()))
    setError(undefined)
    setNotice(undefined)
    setSaving(true)
    try {
      await api.savePlan(selected.id, { field_id, season, crops })
      setEditing(false)
      setNotice('Crop sequence saved.')
      rotations.reload()
    } catch (failure) { setError(errorText(failure)) }
    finally { setSaving(false) }
  }

  return (
    <div className="page-body rotations-page">
      <PageHeading title="Rotations" season={season} dataLabel={result ? 'Historical reanalysis' : 'Demo data'} />
      <FieldWorkspaceBar field={field} fields={fields} chooseField={chooseField} openFields={openFields} disabled={saving} directSelection>
        {onAddField && <button className="button" type="button" disabled={saving} onClick={onAddField}>
          <PlusIcon aria-hidden="true" />{t('Add field')}
        </button>}
        <fieldset className="rotation-context-options" disabled={saving}>
          <ScenarioSelect value={scenario} onChange={chooseScenario} />
          <span className="rotation-irrigation">{irrigationAvailable ? t('Irrigation available') : t('No irrigation')}</span>
        </fieldset>
      </FieldWorkspaceBar>
      <div className="rotations-workspace">
        {fieldSetup.error && <ErrorBox message={fieldSetup.error} retry={fieldSetup.reload} />}
        {fieldSetup.data?.demo_plan && <OnboardingPlan setup={fieldSetup.data} />}
        {!fieldSetup.pending && <RotationTools fold={Boolean(fieldSetup.data?.demo_plan)}>
        <FarmRotationPlanner fieldId={field_id} season={season} onApplied={rotations.reload} />
        {create.error && <ErrorBox message={create.error} />}
        {jobId && <JobStatus job={current.job} error={current.error} />}
        {rotations.pending && <Loading text={t('Loading rotation plans…')} />}
        {rotations.error && <ErrorBox message={rotations.error} retry={rotations.reload} />}
        {error && <ErrorBox message={error} />}
        {notice && <p ref={saveNotice} tabIndex={-1} className="job-status rotation-save-notice" role="status"><CheckCircleIcon aria-hidden="true" />{t(notice)}</p>}
        {data && !data.plans.length && <Empty>{t('No rotation plans are available for this field.')}</Empty>}
        {data && data.plans.length > 0 && (
          <>
            <section className="rotation-planning" aria-labelledby="rotation-planning-heading">
              <div className="section-heading"><div><h2 id="rotation-planning-heading">{t('Choose a crop sequence')}</h2>
                <p>{t('Compare crop sequences for')} {field.name}.</p></div>
                <span className="rotation-period">{years[0]}–{years[years.length - 1]}</span>
              </div>
              <div className="rotation-plan-options" role="group" aria-label={t('Crop sequence')}>
                {data.plans.map((plan) => (
                  <button className="rotation-plan-card" key={plan.id} type="button" aria-pressed={selected?.id === plan.id}
                    disabled={saving}
                    onClick={() => { setSelectedId(plan.id); setEditing(false) }}>
                    <span className="rotation-plan-card-heading">
                      <span className="rotation-selection-mark" aria-hidden="true">{selected?.id === plan.id && <CheckCircleIcon />}</span>
                      <strong>{t(plan.name)}</strong>
                      {plan.id === data.main_plan_id && <small>{t('Main plan')}</small>}
                    </span>
                    <span className="rotation-crop-timeline">
                      {years.map((year) => {
                        const entry = plan.crops.find((item) => item.year === year)
                        return <span className="rotation-crop-stop" key={year}>
                          <span className="rotation-crop-year">{year}</span>
                          <span className="rotation-crop-illustration"><CropIcon crop={entry?.crop ?? ''} /></span>
                          <strong>{entry ? crop(entry.crop) : t('Unassigned')}</strong>
                        </span>
                      })}
                    </span>
                  </button>
                ))}
              </div>
              {selected && <div className="rotation-plan-actions">
                <p><span>{t('Selected plan')}</span><strong>{t(selected.name)}</strong></p>
                <button ref={editButton} className="button" type="button" disabled={saving || !canEdit} onClick={() => setEditing(!editing)}
                  aria-expanded={editing} aria-controls="rotation-plan-editor">
                  <PencilSquareIcon aria-hidden="true" />{editing ? t('Close editor') : t('Edit crop sequence')}
                </button>
                <button type="button" className="button primary" disabled={saving || !canEdit || selected.id === data.main_plan_id}
                  onClick={() => void markMain(selected.id)}>
                  <CheckCircleIcon aria-hidden="true" />{saving ? t('Saving…') : t(selected.id === data.main_plan_id ? 'Main plan' : 'Set as main plan')}
                </button>
              </div>}
              {editing && selected && (
                <form id="rotation-plan-editor" className="form-grid rotation-plan-editor" key={selected.id} onSubmit={(event) => void editPlan(event)}>
                  {selected.crops.map(({ year, crop: cropName }) => (
                    <label key={year}><span>{t('Crop')} · {year}</span><input name={'crop-' + year}
                      defaultValue={cropInput(crop(cropName)).toLowerCase() === cropName.toLowerCase() ? crop(cropName) : cropName}
                      required maxLength={80} disabled={saving} /></label>
                  ))}
                  <div className="full-row rotation-editor-actions">
                    <button className="button" type="button" disabled={saving} onClick={() => { setEditing(false); editButton.current?.focus() }}>{t('Cancel')}</button>
                    <button className="button primary" type="submit" disabled={saving}>{saving ? t('Saving…') : t('Save crop sequence')}</button>
                  </div>
                </form>
              )}
            </section>
            <section className="rotation-water-comparison" aria-labelledby="rotation-water-heading">
              <div className="section-heading">
                <div><h2 id="rotation-water-heading">{t('Water stress · Year 1')}</h2><p>{t('Fewer stress days means less water pressure on the crop.')}</p></div>
                <button className="button" type="button" disabled={!canEdit || !data || saving || create.submitting || active}
                  onClick={() => void runHistoricalComparison()}>
                  <ArrowPathIcon className={active ? 'spin' : undefined} aria-hidden="true" />
                  {create.submitting ? t('Submitting…') : active ? t('Calculating…') : t('Run historical comparison')}
                </button>
              </div>
              <div className="rotation-comparison-context">
                <span className={'chip ' + (result ? 'source-chip' : 'demo-chip')}>{t(result ? 'Historical reanalysis' : 'Demo data')}</span>
                <p>{result && result.plans[0]
                  ? t('Historical weather') + ' · ERA5 · ' + date(result.plans[0].provenance.start_date)
                    + ' – ' + date(result.plans[0].provenance.end_date) + ' · ' + t('Crop year') + ' ' + result.evaluated_year
                  : t('Demo comparison. Run the historical comparison to use ERA5 weather.')}</p>
              </div>
              <RotationComparison groups={measures.map(({ label, property }) => ({
                label: t(label), unit: t('d'),
                values: data.plans.map((plan) => ({
                  label: t(plan.name),
                  value: (result ? result.plans.find(({ id }) => id === plan.id)?.[property] : plan[property]) ?? null,
                })),
              }))} />
            </section>
            <RotationYieldPanel
              key={JSON.stringify([field_id, season, data.plans.map(({ id, crops }) => [id, crops])])}
              fieldId={field_id} season={season} plans={data.plans} />
            <div className="rotation-footer">
              <button type="button" className="button" disabled={saving} onClick={openWater}>{t('Open water balance')}<ArrowRightIcon aria-hidden="true" /></button>
            </div>
          </>
        )}
        </RotationTools>}
      </div>
    </div>
  )
}

function RotationTools({ fold, children }: { fold: boolean; children: ReactNode }) {
  const { language } = useLocale()
  if (!fold) return <>{children}</>
  return <details className="advanced-rotation-tools">
    <summary>{language === 'uk' ? 'Інші інструменти планування' : 'Other planning tools'}</summary>
    {children}
  </details>
}

function matchComparison(job: Job | undefined, context: FieldContext, plans: Rotations | undefined): HistoricalRotationComparison | undefined {
  const result = job?.result
  if (job?.status !== 'succeeded' || !result || !('analysis' in result) || result.analysis !== 'rotation_comparison' || !plans
    || result.field_id !== context.field_id || result.scenario !== context.scenario || result.season !== context.season
    || result.plans.length !== plans.plans.length) return undefined
  const current = plans.plans.every((plan) => {
    const previous = result.plans.find(({ id }) => id === plan.id)
    return previous?.crops.length === plan.crops.length
      && plan.crops.every(({ year, crop }, index) => previous.crops[index]?.year === year && previous.crops[index]?.crop === crop)
  })
  return current ? result : undefined
}
