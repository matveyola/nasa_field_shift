import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { Field } from '../../api/contracts'
import {
  rotationHandoffApi, rotationStrategyApi, type StrategyCandidate, type StrategyChoice,
  type StrategyContext, type StrategyIrrigation, type StrategyPriority, type StrategyScenario,
} from '../../api/rotationHandoff'
import { ErrorBox, Loading } from '../../shared/ui'
import { TermInfo } from '../../shared/TermInfo'
import { errorText, useResource } from '../../shared/useResource'
import { useAccess } from '../access/AccessProvider'
import { QuickCropComparison } from './QuickCropComparison'
import { SowingDateField } from './SowingDateField'
import { StrategyCandidateCard, StrategyResultCards, useStrategyLabels } from './StrategyResultCards'
import { afterCrop, initialPlantingDate, priorityMetrics, strategyChoiceId, validPlantingDate } from './strategyResults'
import { useStrategyJob } from './useStrategyJob'

type Selection = StrategyChoice & { planting_date: string; metrics: StrategyCandidate }
export type StrategyTab = 'date' | 'scenarios'
type Draft = {
  count: number; priority: StrategyPriority; plantingDate: string
  irrigationStatus: StrategyIrrigation['status']; dailyM3: string; method: string
}

function decision({ job_id, crop_id, choice_id }: Selection): StrategyChoice {
  return { job_id, crop_id, ...(choice_id ? { choice_id } : {}) }
}

function sameScope(item: { field_id: string; geometry_revision: number }, field: Field) {
  return item.field_id === field.id && item.geometry_revision === field.boundary_revision
}

function IrrigationSummary({ irrigation }: { irrigation: StrategyIrrigation }) {
  const { text, number } = useStrategyLabels()
  const status = irrigation.status === 'none' ? text('None', 'Немає') : irrigation.status === 'unknown' ? text('Unknown', 'Невідомо')
    : irrigation.daily_m3 == null ? text('Available; daily volume unknown', 'Доступний, добовий обсяг невідомий')
      : `${number(irrigation.daily_m3)} ${text('m³/day', 'м³/добу')}`
  return <p>{text('Irrigation', 'Полив')}: {status}{irrigation.method && ` · ${irrigation.method}`}</p>
}

export function StrategyWorkspace({ field, tab }: { field: Field; tab: StrategyTab }) {
  const { text, cropName } = useStrategyLabels()
  const [saveRevision, setSaveRevision] = useState(0)
  const [started, setStarted] = useState(false)
  const onSave = () => setSaveRevision(revision => revision + 1)
  const loadContext = useCallback(async (signal: AbortSignal) => {
    const context = await rotationStrategyApi.context(field.id, signal)
    if (!sameScope(context, field)) throw new Error('Field or boundary changed. Reload the field.')
    return context
  }, [field.id, field.boundary_revision])
  const fieldContext = useResource(loadContext)
  const model = useResource(rotationHandoffApi.catalog)
  const context = fieldContext.data
  const catalog = model.data
  const cultivarReferences = (catalog?.cultivar_references ?? []).filter(
    row => typeof row.name === 'string' && typeof row.crop_id === 'string',
  )
  const ready = catalog?.status === 'available' && catalog.operations.includes('climate_step')
  const crops = (catalog?.crops ?? []).filter(crop => crop.strategy_supported ?? crop.climate_supported)
  function flow(comparison: boolean) {
    if (fieldContext.pending) return <Loading text={text('Loading field…', 'Завантажуємо поле…')} />
    if (!context) return <ErrorBox message={fieldContext.error ?? text('Field context unavailable', 'Дані поля недоступні')} retry={fieldContext.reload} />
    return <>
      {model.pending ? <Loading text={text('Checking model availability…', 'Перевіряємо доступність моделі…')} /> : !ready && <div className="strategies-notice" role="status">
        <p>{text('The rotation model is temporarily unavailable.', 'Модель сівозмін тимчасово недоступна.')}</p>
        {catalog?.reasons.map(reason => <p key={reason}>{reason}</p>)}
        <button type="button" className="button" onClick={model.reload}>{text('Retry connection', 'Перевірити з’єднання')}</button>
      </div>}
      {comparison ? <QuickCropComparison field={field} context={context} crops={crops} hybrids={catalog?.strategy_hybrids ?? []} ready={ready} />
        : <StrategyFlow field={field} context={context} ready={ready} onSave={onSave} />}
    </>
  }
  return <>
    <div id="strategy-panel-date" role="tabpanel" aria-labelledby="strategy-tab-date" hidden={tab !== 'date'}>{flow(true)}</div>
    <div id="strategy-panel-scenarios" role="tabpanel" aria-labelledby="strategy-tab-scenarios" hidden={tab !== 'scenarios'}>
      <details className="strategy-new" onToggle={event => { if (event.currentTarget.open) setStarted(true) }}>
        <summary>{text('Plan a crop rotation', 'Спланувати сівозміну')}</summary>
        {started && flow(false)}
      </details>
      {cultivarReferences.length > 0 && <details>
        <summary>{text('Variety and hybrid references', 'Довідник сортів і гібридів')} · {cultivarReferences.length}</summary>
        <p>{text('The rotation estimates development separately for selected maize and sunflower hybrids. This reference list also includes varieties from other field trials.',
          'Сівозміна окремо оцінює розвиток деяких гібридів кукурудзи й соняшнику. У цьому довіднику є також сорти з інших польових випробувань.')}</p>
        <ul>{cultivarReferences.map(row => <li key={`${row.crop_id}-${row.name}`}>
          {cropName(String(row.crop_id))} · {String(row.name)}
          {typeof row.trial_location === 'string' && ` · ${row.trial_location}`}
        </li>)}</ul>
      </details>}
      <section className="strategy-saved">
        <h2>{text('Saved crop rotations', 'Збережені сівозміни')}</h2>
        {tab === 'scenarios' && <SavedScenarioList key={saveRevision} field={field} />}
      </section>
    </div>
  </>
}

function StrategyFlow({ field, context, ready, onSave }: {
  field: Field; context: StrategyContext; ready: boolean; onSave: () => void
}) {
  const { text, candidateName, priorityName, date, number } = useStrategyLabels()
  const { canEdit } = useAccess()
  const [draft, setDraft] = useState<Draft>(() => ({ count: 1, priority: 'water_deficit', plantingDate: initialPlantingDate(context),
    irrigationStatus: 'unknown', dailyM3: '', method: '' }))
  const [choices, setChoices] = useState<Selection[]>([])
  const [saved, setSaved] = useState<StrategyScenario>()
  const [saveError, setSaveError] = useState<string>()
  const [saving, setSaving] = useState(false)
  const saveController = useRef<AbortController | undefined>(undefined)
  const calculation = useStrategyJob(field)
  useEffect(() => () => saveController.current?.abort(), [])

  const count = draft.count
  const complete = choices.length === count
  const previous = choices.at(-1)
  const firstDate = choices[0]?.planting_date
  const next = previous && firstDate ? afterCrop(context, previous.metrics, previous.planting_date, firstDate) : undefined
  const dailyM3 = Number(draft.dailyM3)
  const irrigationValid = !draft.dailyM3 || (dailyM3 > 0 && dailyM3 <= 1_000_000)
  const dateValid = validPlantingDate(draft.plantingDate, context, next?.previousEnd, firstDate)
  const canCalculate = ready && canEdit && !calculation.busy && !saving && !complete && dateValid &&
    (draft.irrigationStatus !== 'available' || irrigationValid)
  const irrigation: StrategyIrrigation = draft.irrigationStatus === 'available' ? {
    status: 'available', ...(draft.dailyM3 ? { daily_m3: dailyM3 } : {}), ...(draft.method.trim() ? { method: draft.method.trim() } : {}),
  } : { status: draft.irrigationStatus }

  function edit(patch: Partial<Draft>, restart = true) {
    calculation.clear(); setSaved(undefined); setSaveError(undefined)
    if (restart) setChoices([])
    setDraft(current => ({ ...current, ...(restart && choices.length > 0 ? { plantingDate: choices[0].planting_date } : {}), ...patch }))
  }

  function changeDate(value: string) { edit({ plantingDate: value }, false) }

  function assess(plantingDate: string, selected: Selection[]) {
    void calculation.send({ request_id: crypto.randomUUID(), geometry_revision: field.boundary_revision,
      mode: 'sequence', planting_date: plantingDate, step_count: count, priority: draft.priority,
      previous: selected.map(decision), irrigation,
    })
  }

  function calculate(event: FormEvent) {
    event.preventDefault()
    if (canCalculate) assess(draft.plantingDate, choices)
  }

  function choose(candidate: StrategyCandidate) {
    const jobId = calculation.job?.job_id
    const plantingDate = calculation.plantingDate
    if (!jobId || !plantingDate || complete || !ready || !canEdit) return
    const choiceId = strategyChoiceId(candidate)
    const updated = [...choices, {
      job_id: jobId, crop_id: candidate.crop_id, planting_date: plantingDate, metrics: candidate,
      ...(choiceId !== candidate.crop_id ? { choice_id: choiceId } : {}),
    }]
    setChoices(updated); calculation.clear()
    if (updated.length < count) {
      const nextDate = afterCrop(context, candidate, plantingDate, updated[0].planting_date).suggested
      setDraft(current => ({ ...current, plantingDate: nextDate ?? '' }))
      if (nextDate) assess(nextDate, updated)
    }
  }

  function revise(index: number) {
    const plantingDate = choices[index].planting_date
    const prefix = choices.slice(0, index)
    calculation.clear(); setSaved(undefined); setSaveError(undefined)
    setDraft(current => ({ ...current, plantingDate }))
    setChoices(prefix)
    if (ready && canEdit && prefix.length) assess(plantingDate, prefix)
  }

  async function save() {
    if (!complete || saving || saved || !canEdit || saveController.current) return
    const controller = new AbortController()
    saveController.current = controller
    setSaving(true); setSaveError(undefined)
    try {
      const scenario = await rotationStrategyApi.save(field.id, field.boundary_revision, choices.map(decision), controller.signal)
      if (!sameScope(scenario, field)) throw new Error('Field or boundary changed. Reload the field.')
      if (!controller.signal.aborted) { setSaved(scenario); onSave() }
    } catch (failure) {
      if (!controller.signal.aborted) setSaveError(errorText(failure))
    } finally {
      if (!controller.signal.aborted) setSaving(false)
      saveController.current = undefined
    }
  }

  const result = calculation.job?.result
  const settingUp = choices.length === 0
  const recoveryCases = Object.values(previous?.metrics.scenarios ?? {})
  const recoveryDays = recoveryCases.flatMap(({ post_crop_recharge }) =>
    typeof post_crop_recharge?.days_after_modeled_crop_end === 'number'
      ? [post_crop_recharge.days_after_modeled_crop_end] : [],
  )
  const recoveryRange = recoveryDays.length > 0 && recoveryDays.length === recoveryCases.length
    ? [...new Set([Math.min(...recoveryDays), Math.max(...recoveryDays)])].map(value => number(value, 0)).join('–')
    : null
  return <section className="strategy-flow">
    {!complete && <p role="status">{text(`Season ${choices.length + 1} of ${count} in the rotation`, `Сезон ${choices.length + 1} із ${count} у сівозміні`)}</p>}
    {choices.length > 0 && <ol className="strategy-chosen" aria-label={text('Crops selected for the rotation', 'Обрані культури сівозміни')}>{choices.map((choice, index) => <li key={choice.job_id}>
      <strong>{candidateName(choice.metrics)}</strong> · <time dateTime={choice.planting_date}>{date(choice.planting_date)}</time>
      <button type="button" className="button" disabled={saving} onClick={() => revise(index)} aria-label={text(`Change crop ${index + 1} in the rotation`, `Змінити культуру ${index + 1} у сівозміні`)}>{text('Change', 'Змінити')}</button>
    </li>)}</ol>}
    {!complete && settingUp && <form className="strategy-form" onSubmit={calculate}>
      <div className="strategies-context">
        <div className="strategy-input-control"><div className="context-help-row">
          <label htmlFor="rotation-season-count">{text('Seasons in the rotation', 'Кількість сезонів у сівозміні')}</label>
          <TermInfo term="seasons" label={text('Seasons in the rotation', 'Кількість сезонів у сівозміні')} />
        </div><select id="rotation-season-count" value={draft.count} onChange={event => edit({ count: Number(event.target.value) })}>
          {Array.from({ length: context.max_steps }, (_, index) => <option key={index + 1}>{index + 1}</option>)}</select></div>
        <div className="strategy-input-control"><div className="context-help-row">
          <label htmlFor="rotation-priority">{text('Priority', 'Пріоритет')}</label>
          <TermInfo term={draft.priority} label={priorityName(draft.priority)} />
        </div><select id="rotation-priority" value={draft.priority} onChange={e => edit({ priority: e.target.value as StrategyPriority })}>
          {(Object.keys(priorityMetrics) as StrategyPriority[]).map(priority => <option key={priority} value={priority}>{priorityName(priority)}</option>)}
        </select></div>
        <SowingDateField id="rotation-planting-date" context={context} value={draft.plantingDate}
          onChange={changeDate} />
      </div>
      {draft.priority === 'revenue_after_seed' && <p>{text('Gross revenue minus seed or planting material cost, per hectare.', 'Виручка мінус вартість насіння або посадкового матеріалу, на гектар.')}</p>}
      <details>
        <summary>{text('Irrigation (optional)', 'Полив (необов’язково)')}</summary>
        <p>{text('For a better assessment, specify irrigation availability.', 'Для кращого прогнозу уточніть можливості поливу')}</p>
        <div className="strategies-context">
          <label>{text('Irrigation availability', 'Доступність поливу')}<select value={draft.irrigationStatus} onChange={e => edit({ irrigationStatus: e.target.value as StrategyIrrigation['status'] })}>
            <option value="unknown">{text('Unknown', 'Невідомо')}</option><option value="none">{text('No irrigation', 'Поливу немає')}</option>
            <option value="available">{text('Irrigation available', 'Полив доступний')}</option>
          </select></label>
          {draft.irrigationStatus === 'available' && <>
            <label>{text('Available water, m³/day', 'Доступна вода, м³/добу')}<input type="number" inputMode="decimal" min="0" max="1000000" step="any" value={draft.dailyM3} onChange={e => edit({ dailyM3: e.target.value })} /></label>
            <label>{text('Irrigation method, if known', 'Спосіб поливу, якщо відомий')}<input value={draft.method} maxLength={40} onChange={e => edit({ method: e.target.value })} /></label>
          </>}
        </div>
        {draft.irrigationStatus === 'available' && draft.dailyM3 && irrigationValid && context.area_ha > 0 && <p>{text('Equivalent field depth', 'Еквівалентна глибина для поля')}: {number(dailyM3 / (10 * context.area_ha), 2)} {text('mm/day', 'мм/добу')}</p>}
      </details>
      <button type="submit" className="button primary" disabled={!canCalculate}>{text('Assess crops for the rotation', 'Оцінити культури для сівозміни')}</button>
    </form>}
    {!complete && next && <>
      <p>{text('Next sowing date', 'Наступна дата сівби')} · {dateValid
        ? <time dateTime={draft.plantingDate}>{date(draft.plantingDate)}</time>
        : text('Choose a date', 'Оберіть дату')}</p>
      {next.suggested && next.recoveredOn ? <p>
        {text(
          `Suggested sowing date: ${date(next.suggested)}. The soil reaches 80% of its available water capacity by ${date(next.recoveredOn)} in all scenarios.`,
          `Рекомендована дата сівби — ${date(next.suggested)}. В усіх сценаріях ґрунт відновлює запас води до 80% від доступної місткості до ${date(next.recoveredOn)}.`,
        )}{' '}
        {next.suggested !== next.dayAfterRecovery
          ? text('The next day falls outside the modelled sowing window, so the date moves to its first day.',
            'Наступний день поза періодом сівби, який підтримує модель, тому обрано перший день цього періоду.')
          : text('Sowing starts the following day.', 'Сівбу пропонуємо на наступний день.')}
        {dateValid && draft.plantingDate !== next.suggested && <> {text('You changed the date manually.', 'Ви змінили дату вручну.')}</>}
      </p> : <p>{next.recoveredOn
        ? text('There is no supported sowing date after the estimated recovery within this rotation’s time range.',
          'Після оціненого відновлення в межах цієї сівозміни немає дати сівби, яку підтримує модель.')
        : next.searchedThrough
          ? text(`Water recovery was not confirmed in all scenarios through ${date(next.searchedThrough)}. Choose a sowing date to continue.`,
            `До ${date(next.searchedThrough)} запас води відновився не в усіх сценаріях. Оберіть дату сівби, щоб продовжити.`)
          : text('The model returned no water recovery date. Choose a sowing date to continue.',
            'Модель не визначила дату відновлення запасу води. Оберіть дату сівби, щоб продовжити.')}</p>}
      {recoveryRange && <div className="context-help-row">
        <span>{text(`Estimated water recovery to 80% after the previous crop: ${recoveryRange} days`,
          `Оцінене відновлення запасу води до 80% після попередньої культури — ${recoveryRange} дн.`)}</span>
        <TermInfo term="recharge" label={text('Water recovery', 'Відновлення запасу води')} />
      </div>}
      <details key={choices.length} open={!next.suggested || undefined}>
        <summary>{text('Change sowing date', 'Змінити дату сівби')}</summary>
        <form className="strategy-form" onSubmit={calculate}>
          <div className="strategies-context">
            <SowingDateField id="rotation-next-planting-date" context={context} value={draft.plantingDate}
              onChange={changeDate} previousEnd={next.previousEnd} first={firstDate} />
          </div>
          <button type="submit" className="button" disabled={!canCalculate}>{text('Recalculate for this date', 'Перерахувати на цю дату')}</button>
        </form>
      </details>
    </>}
    {calculation.busy && !calculation.error && <Loading text={settingUp
      ? text('Assessing this field…', 'Оцінюємо це поле…')
      : text('Assessing the next crop…', 'Оцінюємо варіанти наступної культури…')} />}
    {calculation.error && <ErrorBox message={calculation.error} retry={calculation.retry} />}
    {calculation.job?.status === 'failed' && <ErrorBox message={calculation.job.error ?? text('The assessment failed. Try again.', 'Розрахунок не вдався. Спробуйте ще раз.')}
      retry={canCalculate ? () => assess(draft.plantingDate, choices) : undefined} />}
    {result && <>
      {settingUp && result.status === 'available' && result.payload.observed_previous_crop === null && <p>
        {text('Assessment completed with the previous crop unknown.', 'Оцінку виконано за невідомої попередньої культури.')}
      </p>}
      <StrategyResultCards result={result} priority={draft.priority} choose={choose} />
    </>}
    {complete && <section className="strategy-result">
      <h3>{text('Completed crop rotation', 'Завершена сівозміна')}</h3><p>{priorityName(draft.priority)} · {text('Conditional climate scenario', 'Умовний кліматичний сценарій')}</p>
      <IrrigationSummary irrigation={irrigation} />
      <div className="strategy-decisions">{choices.map(choice => <div key={choice.job_id}><p><time dateTime={choice.planting_date}>{date(choice.planting_date)}</time></p>
        <StrategyCandidateCard candidate={choice.metrics} priority={draft.priority} /></div>)}</div>
      {saved ? <p role="status">{text('Crop rotation saved', 'Сівозміну збережено')}</p> : canEdit && <button type="button" className="button primary" disabled={saving} onClick={() => void save()}>{text('Save crop rotation', 'Зберегти сівозміну')}</button>}
      {saveError && <ErrorBox message={saveError} retry={() => void save()} />}
    </section>}
  </section>
}

function SavedScenarioList({ field }: { field: Field }) {
  const { text, date, candidateName, priorityName } = useStrategyLabels()
  const [page, setPage] = useState(0)
  const pageSize = 20
  const load = useCallback(async (signal: AbortSignal) => {
    const rows = await rotationStrategyApi.scenarios(field.id, page * pageSize, pageSize + 1, signal)
    if (rows.some(row => !sameScope(row, field))) throw new Error('Field or boundary changed. Reload the field.')
    return rows
  }, [field.id, field.boundary_revision, page])
  const resource = useResource(load)
  if (resource.pending) return <Loading />
  if (resource.error) return <ErrorBox message={resource.error} retry={resource.reload} />
  const rows = resource.data ?? []
  return <>
    {!rows.length && <p>{text('No saved crop rotations yet.', 'Збережених сівозмін поки немає.')}</p>}
    <div className="strategy-saved-pages">
      <button type="button" className="button" onClick={resource.reload}>{text('Refresh saved crop rotations', 'Оновити збережені сівозміни')}</button>
      {page > 0 && <button type="button" className="button" onClick={() => setPage(page - 1)}>{text('Previous page', 'Попередня сторінка')}</button>}
      {rows.length > pageSize && <button type="button" className="button" onClick={() => setPage(page + 1)}>{text('Next page', 'Наступна сторінка')}</button>}
    </div>
    {rows.slice(0, pageSize).map(scenario => <details key={scenario.id}><summary>{date(scenario.created_at, true)} · {scenario.choices.map(choice => candidateName(choice.metrics)).join(' → ')}</summary>
      <p>{priorityName(scenario.priority)}</p>
      <IrrigationSummary irrigation={scenario.irrigation} />
      <ul>{scenario.assumptions.map((assumption, index) => <li key={index}>{assumption}</li>)}</ul>
      <div className="strategy-decisions">{scenario.choices.map(choice => <div key={choice.job_id}><p><time dateTime={choice.planting_date}>{date(choice.planting_date)}</time></p>
        <StrategyCandidateCard candidate={choice.metrics} priority={scenario.priority} /></div>)}</div>
    </details>)}</>
}
