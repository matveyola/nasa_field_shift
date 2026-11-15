import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { Field } from '../../api/contracts'
import {
  rotationHandoffApi, rotationStrategyApi, type HandoffCatalog, type StrategyCandidate, type StrategyChoice,
  type StrategyContext, type StrategyIrrigation, type StrategyMode, type StrategyPriority, type StrategyScenario,
} from '../../api/rotationHandoff'
import { ErrorBox, Loading } from '../../shared/ui'
import { errorText, useResource } from '../../shared/useResource'
import { useAccess } from '../access/AccessProvider'
import { StrategyCandidateCard, StrategyResultCards, useStrategyLabels } from './StrategyResultCards'
import { nextPlantingDate, plantingBounds, priorityMetrics, validPlantingDate } from './strategyResults'
import { useStrategyJob } from './useStrategyJob'

type Selection = StrategyChoice & { planting_date: string; metrics: StrategyCandidate }
type Draft = {
  count: number; priority: StrategyPriority; plantingDate: string; historyCrop: string; historyDate: string
  irrigationStatus: StrategyIrrigation['status']; dailyM3: string; method: string; crops: [string, string]
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

export function StrategyWorkspace({ field, mode }: { field: Field; mode: StrategyMode }) {
  const { text } = useStrategyLabels()
  const [saveRevision, setSaveRevision] = useState(0)
  const load = useCallback(async (signal: AbortSignal) => {
    const [context, catalog] = await Promise.all([rotationStrategyApi.context(field.id, signal), rotationHandoffApi.catalog(signal)])
    if (!sameScope(context, field)) throw new Error('Field or boundary changed. Reload the field.')
    return { context, catalog }
  }, [field.id, field.boundary_revision])
  const resource = useResource(load)
  if (resource.pending) return <Loading text={text('Loading field history and model…', 'Завантажуємо історію поля та модель…')} />
  if (resource.error || !resource.data) return <ErrorBox message={resource.error ?? text('Field context unavailable', 'Дані поля недоступні')} retry={resource.reload} />
  const { context, catalog } = resource.data
  const ready = catalog.status === 'available' && catalog.operations.includes('climate_step')
  return <>
    {!ready && <div className="strategies-notice" role="status"><p>{text('The rotation model is unavailable.', 'Модель сівозмін недоступна.')}</p>
      {catalog.reasons.map(reason => <p key={reason}>{reason}</p>)}<button type="button" className="button" onClick={resource.reload}>{text('Refresh availability', 'Оновити доступність')}</button></div>}
    <p className="strategy-context-note">{text(`Conditional climate scenario for ${context.min_year}–${context.max_year}. Sowing months ${context.min_month}–${context.max_month}, one sowing per year, up to ${context.max_calendar_years} calendar years per scenario.`,
      `Умовний кліматичний сценарій на ${context.min_year}–${context.max_year} рр. Місяці сівби ${context.min_month}–${context.max_month}, одна сівба на рік, до ${context.max_calendar_years} календарних років у сценарії.`)}</p>
    <div hidden={mode !== 'sequence'} inert={mode !== 'sequence'} id="strategy-sequence"><StrategyFlow field={field} context={context} catalog={catalog} ready={ready} mode="sequence" onSave={() => setSaveRevision(revision => revision + 1)} /></div>
    <div hidden={mode !== 'compare'} inert={mode !== 'compare'} id="strategy-compare"><StrategyFlow field={field} context={context} catalog={catalog} ready={ready} mode="compare" /></div>
    <SavedScenarios field={field} revision={saveRevision} />
  </>
}

function StrategyFlow({ field, context, catalog, mode, ready, onSave }: {
  field: Field; context: StrategyContext; catalog: HandoffCatalog; mode: StrategyMode; ready: boolean; onSave?: () => void
}) {
  const { text, cropName, priorityName, date, number } = useStrategyLabels()
  const { canEdit } = useAccess()
  const sequence = mode === 'sequence'
  const latest = context.history[0]
  const [draft, setDraft] = useState<Draft>(() => ({ count: 1, priority: 'water_deficit', plantingDate: nextPlantingDate(context, 1),
    historyCrop: '', historyDate: '', irrigationStatus: 'unknown', dailyM3: '', method: '', crops: ['', ''] }))
  const [choices, setChoices] = useState<Selection[]>([])
  const [saved, setSaved] = useState<StrategyScenario>()
  const [saveError, setSaveError] = useState<string>()
  const [saving, setSaving] = useState(false)
  const saveController = useRef<AbortController | undefined>(undefined)
  const calculation = useStrategyJob(field)
  useEffect(() => () => saveController.current?.abort(), [])

  const count = sequence ? draft.count : 1
  const remaining = Math.max(1, count - choices.length)
  const complete = sequence && choices.length === count
  const previousDate = choices.at(-1)?.planting_date
  const firstDate = choices[0]?.planting_date
  const bounds = plantingBounds(context, remaining, previousDate, firstDate)
  const dailyM3 = Number(draft.dailyM3)
  const irrigationValid = !draft.dailyM3 || (dailyM3 > 0 && dailyM3 <= 1_000_000)
  const historyDate = latest?.sowing_date || draft.historyDate
  const historyDateValid = !historyDate || historyDate < draft.plantingDate
  const historyComplete = latest != null || Boolean(draft.historyCrop) === Boolean(draft.historyDate)
  const comparisonValid = sequence || (draft.crops.every(Boolean) && draft.crops[0] !== draft.crops[1])
  const canCalculate = ready && canEdit && !calculation.busy && !saving && !complete && comparisonValid && historyDateValid && historyComplete &&
    validPlantingDate(draft.plantingDate, context, remaining, previousDate, firstDate) &&
    (draft.irrigationStatus !== 'available' || irrigationValid)
  const options = catalog.crops.filter(crop => crop.strategy_supported ?? crop.climate_supported)
  const irrigation: StrategyIrrigation = draft.irrigationStatus === 'available' ? {
    status: 'available', ...(draft.dailyM3 ? { daily_m3: dailyM3 } : {}), ...(draft.method.trim() ? { method: draft.method.trim() } : {}),
  } : { status: draft.irrigationStatus }

  function edit(patch: Partial<Draft>, restart = true) {
    calculation.clear(); setSaved(undefined); setSaveError(undefined)
    if (restart) setChoices([])
    setDraft(current => ({ ...current, ...(restart && choices.length > 0 ? { plantingDate: choices[0].planting_date } : {}), ...patch }))
  }

  function changeDate(value: string) { edit({ plantingDate: value }, false) }

  function calculate(event: FormEvent) {
    event.preventDefault()
    if (!canCalculate) return
    void calculation.send({ request_id: crypto.randomUUID(), geometry_revision: field.boundary_revision,
      mode, planting_date: draft.plantingDate, step_count: count, priority: draft.priority,
      previous: choices.map(({ job_id, crop_id }) => ({ job_id, crop_id })),
      ...(!sequence ? { comparison_crops: draft.crops } : {}),
      ...(!latest?.crop_id && draft.historyCrop ? { history_crop_id: draft.historyCrop } : {}),
      ...(!latest?.sowing_date && draft.historyDate ? { history_sowing_date: draft.historyDate } : {}), irrigation,
    })
  }

  function choose(candidate: StrategyCandidate) {
    const jobId = calculation.job?.job_id
    const plantingDate = calculation.plantingDate
    if (!jobId || !plantingDate || complete) return
    const updated = [...choices, { job_id: jobId, crop_id: candidate.crop_id, planting_date: plantingDate, metrics: candidate }]
    setChoices(updated); calculation.clear()
    if (updated.length < count) setDraft(current => ({ ...current, plantingDate: nextPlantingDate(context, count - updated.length, plantingDate, undefined, updated[0].planting_date) }))
  }

  function revise(index: number) {
    calculation.clear(); setSaved(undefined); setSaveError(undefined)
    setDraft(current => ({ ...current, plantingDate: choices[index].planting_date }))
    setChoices(choices.slice(0, index))
  }

  async function save() {
    if (!complete || saving || saved || !canEdit || saveController.current) return
    const controller = new AbortController()
    saveController.current = controller
    setSaving(true); setSaveError(undefined)
    try {
      const scenario = await rotationStrategyApi.save(field.id, field.boundary_revision, choices.map(({ job_id, crop_id }) => ({ job_id, crop_id })), controller.signal)
      if (!sameScope(scenario, field)) throw new Error('Field or boundary changed. Reload the field.')
      if (!controller.signal.aborted) { setSaved(scenario); onSave?.() }
    } catch (failure) {
      if (!controller.signal.aborted) setSaveError(errorText(failure))
    } finally {
      if (!controller.signal.aborted) setSaving(false)
      saveController.current = undefined
    }
  }

  const result = calculation.job?.result
  return <section className="strategy-flow" aria-labelledby={`strategy-title-${mode}`}>
    <h2 id={`strategy-title-${mode}`}>{sequence ? text('Step-by-step rotation', 'Покрокова сівозміна') : text('Compare two crops', 'Порівняння двох культур')}</h2>
    {sequence && !complete && <p role="status">{text(`Step ${choices.length + 1} of ${count}`, `Крок ${choices.length + 1} із ${count}`)}</p>}
    {choices.length > 0 && <ol className="strategy-chosen" aria-label={text('Chosen sequence', 'Обрана послідовність')}>{choices.map((choice, index) => <li key={choice.job_id}>
      <strong>{cropName(choice.crop_id)}</strong> · <time dateTime={choice.planting_date}>{date(choice.planting_date)}</time>
      <button type="button" className="button" disabled={saving} onClick={() => revise(index)} aria-label={text(`Change step ${index + 1}`, `Змінити крок ${index + 1}`)}>{text('Change', 'Змінити')}</button>
    </li>)}</ol>}
    {!complete && <form className="strategy-form" onSubmit={calculate}>
      <div className="strategies-context">
        {sequence && <label>{text('Number of steps', 'Кількість кроків')}<select value={draft.count} onChange={event => {
          const count = Number(event.target.value)
          edit({ count, plantingDate: validPlantingDate(draft.plantingDate, context, count) ? draft.plantingDate : nextPlantingDate(context, count) })
        }}>{Array.from({ length: context.max_steps }, (_, index) => <option key={index + 1}>{index + 1}</option>)}</select></label>}
        <label>{text('Priorities', 'Пріоритети')}<select value={draft.priority} onChange={e => edit({ priority: e.target.value as StrategyPriority })}>
          {(Object.keys(priorityMetrics) as StrategyPriority[]).map(priority => <option key={priority} value={priority}>{priorityName(priority)}</option>)}
        </select></label>
        <label>{text('Assessment / sowing date', 'Дата оцінки / сівби')}<input type="date" required min={bounds.min} max={bounds.max} value={draft.plantingDate} onChange={e => changeDate(e.target.value)} /></label>
        {!sequence && [0, 1].map(index => <label key={index}>{index === 0 ? text('First crop', 'Перша культура') : text('Second crop', 'Друга культура')}
          <select required value={draft.crops[index]} onChange={e => { const crops = [...draft.crops] as [string, string]; crops[index] = e.target.value; edit({ crops }) }}>
            <option value="">{text('Choose a crop', 'Оберіть культуру')}</option>
            {options.map(crop => <option key={crop.id} value={crop.id} disabled={crop.id === draft.crops[1 - index]}>{cropName(crop.id)}</option>)}
          </select></label>)}
      </div>
      {!historyDateValid && <p role="status">{text('Assessment must be after the previous sowing date.', 'Дата оцінки має бути пізнішою за попередню сівбу.')}</p>}
      {!historyComplete && <p role="status">{text('Enter both the previous crop and sowing date or leave both unknown.', 'Укажіть попередню культуру й дату сівби або залиште обидва значення невідомими.')}</p>}
      {draft.priority === 'revenue_after_seed' && <p>{text('Gross revenue minus seed or planting material cost, per hectare.', 'Виручка мінус вартість насіння або посадкового матеріалу, на гектар.')}</p>}
      <div className="strategies-context">
        {latest?.crop_id ? <p><strong>{text('Previous crop', 'Попередня культура')}</strong><span>{cropName(latest.crop_id)} · {latest.season_year}</span></p>
          : <label>{text('Previous crop, if known', 'Попередня культура, якщо відома')}<select value={draft.historyCrop} onChange={e => edit({ historyCrop: e.target.value })}>
            <option value="">{text('Unknown', 'Невідомо')}</option>
            {catalog.crops.map(crop => <option key={crop.id} value={crop.id}>{cropName(crop.id)}</option>)}
          </select></label>}
        {latest?.sowing_date ? <p><strong>{text('Previous sowing date', 'Дата попередньої сівби')}</strong><span><time dateTime={latest.sowing_date}>{date(latest.sowing_date)}</time></span></p>
          : <label>{text('Previous sowing date, if known', 'Дата попередньої сівби, якщо відома')}<input type="date" value={draft.historyDate} onChange={e => edit({ historyDate: e.target.value })} /></label>}
      </div>
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
      <button type="submit" className="button primary" disabled={!canCalculate}>{sequence ? text('Calculate this step', 'Розрахувати цей крок') : text('Compare these crops', 'Порівняти ці культури')}</button>
    </form>}
    {calculation.busy && !calculation.error && <Loading text={text('Assessing this field…', 'Оцінюємо це поле…')} />}
    {calculation.error && <ErrorBox message={calculation.error} retry={calculation.retry} />}
    {calculation.job?.status === 'failed' && <ErrorBox message={calculation.job.error ?? text('The assessment failed. Try again.', 'Розрахунок не вдався. Спробуйте ще раз.')} />}
    {result && <StrategyResultCards result={result} priority={draft.priority} comparison={sequence ? undefined : draft.crops} choose={sequence ? choose : undefined} />}
    {complete && <section className="strategy-result">
      <h3>{text('Completed scenario', 'Завершений сценарій')}</h3><p>{priorityName(draft.priority)} · {text('Conditional climate scenario', 'Умовний кліматичний сценарій')}</p>
      <IrrigationSummary irrigation={irrigation} />
      <p>{text('Previous crop', 'Попередня культура')}: {latest?.crop_id || draft.historyCrop ? cropName(latest?.crop_id || draft.historyCrop) : text('Unknown', 'Невідомо')}
        {historyDate ? ` · ${date(historyDate)}` : ` · ${text('Sowing date unknown', 'Дата сівби невідома')}`}</p>
      <div className="strategy-decisions">{choices.map(choice => <div key={choice.job_id}><p><time dateTime={choice.planting_date}>{date(choice.planting_date)}</time></p>
        <StrategyCandidateCard candidate={choice.metrics} priority={draft.priority} /></div>)}</div>
      {saved ? <p role="status">{text('Scenario saved', 'Сценарій збережено')}</p> : canEdit && <button type="button" className="button primary" disabled={saving} onClick={() => void save()}>{text('Save completed scenario', 'Зберегти завершений сценарій')}</button>}
      {saveError && <ErrorBox message={saveError} retry={() => void save()} />}
    </section>}
  </section>
}

function SavedScenarios({ field, revision }: { field: Field; revision: number }) {
  const { text } = useStrategyLabels()
  const [open, setOpen] = useState(false)
  return <details className="strategy-saved" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{text('Saved scenarios', 'Збережені сценарії')}</summary>
    {open && <SavedScenarioList key={revision} field={field} />}
  </details>
}

function SavedScenarioList({ field }: { field: Field }) {
  const { text, date, cropName, priorityName } = useStrategyLabels()
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
    {!rows.length && <p>{text('No saved scenarios on this page.', 'На цій сторінці немає збережених сценаріїв.')}</p>}
    <div className="strategy-saved-pages">
      <button type="button" className="button" onClick={resource.reload}>{text('Refresh saved scenarios', 'Оновити збережені сценарії')}</button>
      {page > 0 && <button type="button" className="button" onClick={() => setPage(page - 1)}>{text('Previous page', 'Попередня сторінка')}</button>}
      {rows.length > pageSize && <button type="button" className="button" onClick={() => setPage(page + 1)}>{text('Next page', 'Наступна сторінка')}</button>}
    </div>
    {rows.slice(0, pageSize).map(scenario => <details key={scenario.id}><summary>{date(scenario.created_at, true)} · {scenario.choices.map(choice => cropName(choice.crop_id)).join(' → ')}</summary>
      <p>{priorityName(scenario.priority)}</p>
      <IrrigationSummary irrigation={scenario.irrigation} />
      <ul>{scenario.assumptions.map((assumption, index) => <li key={index}>{assumption}</li>)}</ul>
      <div className="strategy-decisions">{scenario.choices.map(choice => <div key={choice.job_id}><p><time dateTime={choice.planting_date}>{date(choice.planting_date)}</time></p>
        <StrategyCandidateCard candidate={choice.metrics} priority={scenario.priority} /></div>)}</div>
    </details>)}</>
}
