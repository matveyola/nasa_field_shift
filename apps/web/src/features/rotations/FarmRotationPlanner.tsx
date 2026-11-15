import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { ArrowPathIcon, CheckCircleIcon, ChevronDownIcon, LockClosedIcon, SparklesIcon } from '@heroicons/react/24/outline'
import { rotationPlanningApi, type CropDecision, type CropRule, type PlanningContext, type PlanningCrop, type PlanningDraft, type PlanningRequest } from '../../api/rotationPlanning'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { ErrorBox, Loading } from '../../shared/ui'
import { errorText, useResource } from '../../shared/useResource'
import { useAccess } from '../access/AccessProvider'
import { planningUkrainian } from './planningUkrainian'
import './farmRotationPlanner.css'
import { nextSeasonDecisions } from '../strategies/model'

// crops in greys (no hue in the app); each segment is also named in its legend
const colors: Record<PlanningCrop, string> = {
  Wheat: '#0d0d0d', Maize: '#3d3d3d', Soy: '#5c5c5c', Barley: '#8a8a8a',
  Sunflower: '#a6a6a6', Rapeseed: '#c4c4c4', Peas: '#dcdcdc',
}
const defaultRule = (crop: PlanningCrop): CropRule => ({ crop, min_break_years: 1, preference: 0, max_share_percent: 100, min_area_ha: 0, max_area_ha: null })
const defaults: PlanningCrop[] = ['Wheat', 'Maize', 'Soy']
const priorityNotes = {
  water: 'Water is a priority. Compare water stress before applying a plan.',
  soil: 'Soil health is a priority. Review crop return breaks and botanical families.',
  low_change: 'Fewer changes is a priority. Use crop preferences to favor familiar crops.',
} as const

function usePlanningLocale() {
  const locale = useLocale()
  return { ...locale, t: (key: string) => locale.language === 'uk' && planningUkrainian[key] ? planningUkrainian[key] : locale.t(key) }
}

export function FarmRotationPlanner({ season, onApplied, embedded = false, fieldId, focusedField = fieldId, seasonCount, onSeasonCountChange }: { season: number; fieldId?: string; onApplied: () => void; embedded?: boolean; focusedField?: string; seasonCount?: number; onSeasonCountChange?: (count: number) => void }) {
  const { t, language } = usePlanningLocale()
  const [internalCount, setInternalCount] = useState(3)
  const count = seasonCount ?? internalCount
  const changeCount = onSeasonCountChange ?? setInternalCount
  const [open, setOpen] = useState(false)
  return <section className="farm-planner" aria-labelledby="farm-planner-title">
    {!embedded && <div className="section-heading"><div><h2 id="farm-planner-title">{t('Plan across your farm')}</h2>
      <p>{language === 'uk' ? 'Розподіліть культури на обраний період за вашими площами та правилами сівозміни.' : 'Allocate crops for your chosen period, with area limits and rotation rules.'}</p></div>
      <button type="button" className="button" aria-expanded={open} aria-controls="farm-planner-content" onClick={() => setOpen(!open)}>
        <SparklesIcon aria-hidden="true" />{t(open ? 'Close farm planner' : 'Open farm planner')}
      </button>
    </div>}
    {embedded && <h2 id="farm-planner-title" className="sr-only">{t('Plan across your farm')}</h2>}
    {(embedded || open) && <PlannerLoader key={season} startSeason={embedded ? season : Math.min(2101 - count, season + 1)} onApplied={onApplied} embedded={embedded} focusedField={focusedField} seasonCount={count} onSeasonCountChange={changeCount} />}
  </section>
}

function PlannerLoader({ startSeason, onApplied, embedded, focusedField, seasonCount, onSeasonCountChange }: { startSeason: number; onApplied: () => void; embedded?: boolean; focusedField?: string; seasonCount: number; onSeasonCountChange: (count: number) => void }) {
  const { t } = usePlanningLocale()
  const [firstSeason, setFirstSeason] = useState(startSeason)
  // Context provides field history/catalog. A horizon edit does not discard those inputs or the draft editor.
  const initialCount = useRef(seasonCount).current
  const load = useCallback((signal: AbortSignal) => rotationPlanningApi.context(firstSeason, signal, initialCount), [firstSeason, initialCount])
  const context = useResource(load)
  return <div id="farm-planner-content">
    <label className="planner-first-season">{t('First crop season')}<select value={firstSeason} onChange={(event) => setFirstSeason(Number(event.target.value))}>
      {Array.from({ length: 7 }, (_, index) => startSeason - 1 + index).filter((value) => value >= 2001 && value + seasonCount - 1 <= 2100).map((value) => <option key={value} value={value}>{value}–{value + seasonCount - 1}</option>)}
    </select></label>
    {context.pending && <Loading text={t('Loading farm planning data…')} />}
    {context.error && <ErrorBox message={context.error} retry={context.reload} />}
    {context.data && !context.pending && <PlannerEditor key={JSON.stringify([context.data.start_season, context.data.fields])} context={context.data} onApplied={onApplied} refresh={context.reload} embedded={embedded} focusedField={focusedField} seasonCount={seasonCount} onSeasonCountChange={onSeasonCountChange} />}
  </div>
}

function PlannerEditor({ context, onApplied, refresh, embedded, focusedField, seasonCount, onSeasonCountChange }: { context: PlanningContext; onApplied: () => void; refresh: () => void; embedded?: boolean; focusedField?: string; seasonCount: number; onSeasonCountChange: (count: number) => void }) {
  const { t, number, language } = usePlanningLocale()
  const { canEdit } = useAccess()
  const [name, setName] = useState(t('Farm rotation plan'))
  const [selected, setSelected] = useState(context.fields.slice(0, 30).map(({ id }) => id))
  const [rules, setRules] = useState<CropRule[]>(() => embedded ? context.crops.map(({ crop }) => defaultRule(crop)) : defaults.map(defaultRule))
  const [familyBreak, setFamilyBreak] = useState(false)
  const [twoCrops, setTwoCrops] = useState(seasonCount > 1)
  const [history, setHistory] = useState<CropDecision[]>(context.fields.flatMap(({ history }) => history))
  const [locked, setLocked] = useState<CropDecision[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [draft, setDraft] = useState<PlanningDraft>()
  const [confirm, setConfirm] = useState(false)
  const request = useRef<PlanningRequest | undefined>(undefined)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const loadDrafts = useCallback((signal: AbortSignal) => rotationPlanningApi.drafts(signal), [])
  const saved = useResource(loadDrafts)
  const matchingDrafts = saved.data?.filter((value) => value.start_season === context.start_season)
  const selectedFields = context.fields.filter(({ id }) => selected.includes(id))
  const total = selectedFields.reduce((sum, field) => sum + field.area_ha, 0)
  const years = Array.from({ length: seasonCount }, (_, index) => context.start_season + index)

  function changed() { request.current = undefined; setDraft(undefined); setConfirm(false); setError(undefined) }
  function editRule(crop: PlanningCrop, property: keyof Omit<CropRule, 'crop'>, value: number | null) {
    changed(); setRules((current) => current.map((rule) => rule.crop === crop ? { ...rule, [property]: value } : rule))
  }
  function decide(field_id: string, season: number, crop: string, historical: boolean) {
    changed()
    const setter = historical ? setHistory : setLocked
    setter((current) => [...current.filter((item) => item.field_id !== field_id || item.season !== season),
      ...(crop ? [{ field_id, season, crop: crop as PlanningCrop }] : [])])
  }
  function historyChoice(fieldId: string, year: number) {
    const existing = context.fields.find((field) => field.id === fieldId)?.history.find((entry) => entry.season === year)
    const value = history.find((entry) => entry.field_id === fieldId && entry.season === year)?.crop ?? existing?.crop ?? ''
    return <label key={year}>{year}<select value={value} onChange={(event) => decide(fieldId, year, event.target.value, true)}>
      <option value="" disabled={Boolean(existing)}>{t('Unknown')}</option>{context.crops.map(({ crop }) => <option key={crop} value={crop}>{t(crop)}</option>)}</select></label>
  }
  async function generate(event: FormEvent) {
    event.preventDefault()
    if (busy || !canEdit) return
    setError(undefined); setConfirm(false)
    if (!selected.length || rules.length < 1) { setError(language === 'uk' ? 'Оберіть щонайменше одне поле й одну культуру.' : 'Choose at least one field and one crop.'); return }
    if (seasonCount === 1 && twoCrops) { setError(language === 'uk' ? 'Для одного року вимкніть вимогу двох різних культур на кожному полі.' : 'For one year, turn off the requirement for two different crops on each field.'); return }
    const body = request.current ?? {
      client_id: crypto.randomUUID(), name: name.trim(), start_season: context.start_season, season_count: seasonCount,
      field_ids: selected, crops: rules, history: history.filter((entry) => selected.includes(entry.field_id)),
      locked: locked.filter((entry) => selected.includes(entry.field_id)), avoid_consecutive_family: familyBreak, require_two_crops: twoCrops,
    }
    request.current = body
    setBusy(true)
    try {
      const value = await rotationPlanningApi.generate(body)
      if (mounted.current) { setDraft(value); saved.reload() }
    } catch (failure) { if (mounted.current) setError(errorText(failure)) }
    finally { if (mounted.current) setBusy(false) }
  }
  async function openDraft(id: string) {
    if (!id || busy) return
    setBusy(true); setError(undefined); setConfirm(false)
    try {
      const value = await rotationPlanningApi.draft(id)
      if (mounted.current) {
        setDraft(value)
        onSeasonCountChange(value.season_count)
        setName(value.request.name); setSelected(value.request.field_ids)
        setRules(value.request.crops); setHistory(value.request.history); setLocked(value.request.locked)
        setFamilyBreak(value.request.avoid_consecutive_family); setTwoCrops(value.request.require_two_crops)
        // A saved plan retains its own immutable constraints and field snapshot.
        request.current = undefined
      }
    } catch (failure) { if (mounted.current) setError(errorText(failure)) }
    finally { if (mounted.current) setBusy(false) }
  }
  async function apply() {
    if (!draft || busy || !canEdit) return
    setBusy(true); setError(undefined)
    try {
      const applied = await rotationPlanningApi.apply(draft.id)
      if (mounted.current) { setDraft(applied); setConfirm(false); saved.reload(); onApplied() }
    } catch (failure) { if (mounted.current) setError(errorText(failure)) }
    finally { if (mounted.current) setBusy(false) }
  }

  const application = draft && ['optimal', 'feasible'].includes(draft.result.status) && <div className="planner-apply">
      {draft.applied_at ? <p role="status"><CheckCircleIcon aria-hidden="true" />{t('Crop plans saved for every selected field.')}</p>
        : confirm ? <div className="planner-confirm"><strong>{t('Review before applying')}</strong><p>{t('This replaces Plan A and makes it the main plan for every selected field for this planning period. Existing crops and field boundaries are unchanged.')}</p>
          <div><button type="button" className="button" disabled={busy} onClick={() => setConfirm(false)}>{t('Cancel')}</button>
            <button type="button" className="button primary" disabled={busy || !canEdit} onClick={() => void apply()}>{t(busy ? 'Saving…' : 'Confirm crop plan')}</button></div></div>
          : <button type="button" className="button primary" disabled={busy || !canEdit} onClick={() => setConfirm(true)}>{t('Apply to selected fields')}</button>}
    </div>

  return <div className="farm-planner-editor">
    {!context.fields.length && <p role="status">{language === 'uk' ? 'Немає зареєстрованих полів для розподілу. Додайте поле у господарстві.' : 'No registered fields are available for allocation. Add a field to your farm.'}</p>}
    {!context.crops.length && <p role="status">{language === 'uk' ? 'Каталог культур недоступний. Оновіть дані планування.' : 'The crop catalog is unavailable. Refresh planning data.'}</p>}
    <details open={!embedded} className="planner-saved"><summary>{language === 'uk' ? 'Збережені розподіли' : 'Saved allocations'}</summary><div className="planner-saved-row">
      <label>{t('Saved plans')}<select aria-label={t('Saved plans')} disabled={busy || !matchingDrafts?.length} value={draft?.id ?? ''} onChange={(event) => void openDraft(event.target.value)}>
        <option value="">{t(matchingDrafts?.length ? 'Select a saved plan' : 'No saved farm plans yet.')}</option>
        {matchingDrafts?.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.start_season}–{item.start_season + item.season_count - 1} · {t(item.applied_at ? 'Applied' : 'Draft')}</option>)}
      </select></label>
      <button type="button" className="button" disabled={busy} onClick={refresh}><ArrowPathIcon aria-hidden="true" />{t('Refresh planning data')}</button>
    </div>
    {saved.error && <ErrorBox message={saved.error} retry={saved.reload} />}</details>
    {embedded && draft && <PlannerResult draft={draft} focusedField={focusedField} decisionFirst />}
    {embedded && application}
    <form onSubmit={(event) => void generate(event)}>
      <fieldset disabled={busy || !canEdit} className="planner-form-body">
        <label className="strategy-horizon">{language === 'uk' ? 'Років планування розподілу' : 'Allocation planning years'}<select value={seasonCount} onChange={event => { changed(); onSeasonCountChange(Number(event.target.value)) }}>
          {Array.from({ length: context.max_season_count - context.min_season_count + 1 }, (_, index) => context.min_season_count + index).map(count => <option key={count} value={count} disabled={context.start_season + count - 1 > 2100 || locked.some(row => row.season >= context.start_season + count)}>{count}</option>)}
        </select></label>
        {seasonCount === 1 && twoCrops && <p role="status">{language === 'uk' ? 'Вимога двох культур суперечить одному року. Змініть її в обмеженнях або збільште горизонт.' : 'The two-crop requirement conflicts with one year. Change it in constraints or increase the horizon.'}</p>}
        {embedded && <div className="strategy-generate-row"><p><strong>{number(total)} {t('ha')} · {selected.length} {t('Fields')}</strong><br />{language === 'uk' ? 'Виберіть доступні культури нижче. Розрахунок збереже чернетку; застосування потребує підтвердження.' : 'Choose available crops below. Calculation saves a draft; applying it needs confirmation.'}</p><div className="planner-generate"><button className="button primary" disabled={!selected.length || rules.length < 1} type="submit"><SparklesIcon aria-hidden="true" />{t(busy ? 'Planning…' : 'Generate and save draft')}</button></div></div>}
        <details open={!embedded} className="planner-identity"><summary>{language === 'uk' ? 'Поля та назва плану' : 'Fields and plan name'}</summary>
        <div className="planner-name-row"><label>{t('Plan name')}<input required maxLength={120} value={name} onChange={(event) => { changed(); setName(event.target.value) }} /></label>
          <span className="planner-period">{years[0]}–{years.at(-1)}<small>{number(total)} {t('ha')} · {selected.length} {t('Fields')}</small></span>
        </div>
        <fieldset className="planner-fields"><legend>{t('Selected fields')}</legend>
          <div className="planner-field-options">{context.fields.map((field) => <label key={field.id}>
            <input type="checkbox" checked={selected.includes(field.id)} disabled={!selected.includes(field.id) && selected.length >= 30}
              onChange={(event) => { changed(); setSelected(event.target.checked ? [...selected, field.id] : selected.filter((id) => id !== field.id)) }} />
            <span><strong>{field.name}</strong><small>{number(field.area_ha)} {t('ha')}</small></span>
          </label>)}</div><p className="planner-help">{t('Select up to 30 fields. Whole fields are allocated; a field is never split to meet an area target.')}</p>
          {selectedFields.map((field) => field.allowed_crops?.length ? <p className="planner-help" key={field.id}>
            <strong>{field.name}</strong> · {t('Allowed crops from field setup')}: {field.allowed_crops.map(t).join(', ')}.
            {field.priority && <> {t(priorityNotes[field.priority])}</>}
          </p> : null)}
        </fieldset>
        </details>
        {embedded && <fieldset className="strategy-crop-preview planner-fields"><legend>{language === 'uk' ? 'Культури для розподілу' : 'Crops to allocate'}</legend>
          <div className="planner-field-options">{context.crops.map(({ crop }) => <label key={crop}><input type="checkbox" checked={rules.some(rule => rule.crop === crop)} aria-label={t('Enable crop') + ' · ' + t(crop)} onChange={event => {
            changed(); setRules(event.target.checked ? [...rules, defaultRule(crop)] : rules.filter(rule => rule.crop !== crop))
            if (!event.target.checked) setLocked(items => items.filter(item => item.crop !== crop))
          }} /><CropIcon crop={crop} /><strong>{t(crop)}</strong></label>)}</div>
          <p className="planner-help">{language === 'uk' ? 'Це доступний каталог розподілу, а не підтверджена придатність усіх культур для поля. Інші культури та гібриди не підтримуються цим розрахунком. Пріоритети й площі можна змінити нижче.' : 'This is the available allocation catalog, not verified field suitability for every crop. Other crops and hybrids are not supported by this calculation. Edit preferences and area limits below.'}</p>
        </fieldset>}
        <details className="planner-advanced" open={embedded ? undefined : true}><summary>{language === 'uk' ? 'Культури, обмеження та зафіксовані рішення' : 'Crops, constraints and commitments'}</summary>
        <div className="planner-section-title"><h3>{t('Crop rules')}</h3><p>{language === 'uk' ? 'Обмеження діють окремо в кожному обраному році.' : 'Limits apply separately in each selected year.'} {t('Enabled crops are options. Set a minimum area to require a crop each season.')}</p></div>
        <div className="planner-crop-rules">{context.crops.filter(({ crop }) => !embedded || rules.some(rule => rule.crop === crop)).map(({ crop }) => {
          const rule = rules.find((item) => item.crop === crop)
          return <div className={'planner-crop-rule' + (rule ? ' enabled' : '')} key={crop}>
            <label className="planner-crop-enabled">{!embedded && <input type="checkbox" checked={Boolean(rule)} aria-label={t('Enable crop') + ' · ' + t(crop)} onChange={(event) => {
              changed(); setRules(event.target.checked ? [...rules, defaultRule(crop)] : rules.filter((item) => item.crop !== crop))
              if (!event.target.checked) setLocked((items) => items.filter((item) => item.crop !== crop))
            }} />}<CropIcon crop={crop} /><strong>{t(crop)}</strong></label>
            {rule && <div className="planner-rule-inputs">
              <label>{t('Return break, years')}<input type="number" min="0" max="8" step="1" required value={Number.isFinite(rule.min_break_years) ? rule.min_break_years : ''} onChange={(event) => editRule(crop, 'min_break_years', event.target.valueAsNumber)} /></label>
              <label>{t('Preference, 0–10')}<input type="number" min="0" max="10" step="1" required value={Number.isFinite(rule.preference) ? rule.preference : ''} onChange={(event) => editRule(crop, 'preference', event.target.valueAsNumber)} /></label>
              <label>{t('Maximum share, %')}<input type="number" min="0" max="100" step="0.1" required value={Number.isFinite(rule.max_share_percent) ? rule.max_share_percent : ''} onChange={(event) => editRule(crop, 'max_share_percent', event.target.valueAsNumber)} /></label>
              <label>{t('Minimum area, ha')}<input type="number" min="0" max="3000000" step="0.01" required value={Number.isFinite(rule.min_area_ha) ? rule.min_area_ha : ''} onChange={(event) => editRule(crop, 'min_area_ha', event.target.valueAsNumber)} /></label>
              <label>{t('Maximum area, ha')}<input type="number" min={rule.min_area_ha} max="3000000" step="0.01" placeholder={t('No limit')} value={rule.max_area_ha ?? ''} onChange={(event) => editRule(crop, 'max_area_ha', event.target.value ? event.target.valueAsNumber : null)} /></label>
            </div>}
          </div>
        })}</div>
        <p className="planner-help">{t('A return break of 1 means at least one intervening crop year. These are your planning constraints, not crop-specific recommendations.')}</p>
        <div className="planner-switches"><label><input type="checkbox" checked={twoCrops} onChange={(event) => { changed(); setTwoCrops(event.target.checked) }} />{t('At least two different crops on every field')}</label>
          <label><input type="checkbox" checked={familyBreak} onChange={(event) => { changed(); setFamilyBreak(event.target.checked) }} />{t('Avoid the same botanical family in consecutive years')}</label>
          {familyBreak && <p className="planner-help">{t('Wheat, barley and maize share a family; soy and peas share another.')}</p>}
        </div>
        <details className="planner-history"><summary>{t('History and locked decisions')}<ChevronDownIcon aria-hidden="true" /></summary>
          <p>{t('Add known crop history and lock crops that are already decided. Empty history remains unknown.')}</p>
          {selectedFields.map((field) => <div className="planner-history-field" key={field.id}><strong>{field.name}</strong>
            <fieldset><legend>{t('Previous crops')}</legend><div className="planner-year-choices">{[context.start_season - 3, context.start_season - 2, context.start_season - 1].map((year) => historyChoice(field.id, year))}</div>
              <details className="planner-earlier-history"><summary>{t('Earlier crop history')}</summary><div className="planner-year-choices">{Array.from({ length: 5 }, (_, index) => context.start_season - 8 + index).map((year) => historyChoice(field.id, year))}</div></details>
            </fieldset>
            <fieldset><legend>{t('Locked future crops')}</legend><div className="planner-year-choices">{years.map((year) =>
              <label key={year}>{year}<select value={locked.find((entry) => entry.field_id === field.id && entry.season === year)?.crop ?? ''} onChange={(event) => decide(field.id, year, event.target.value, false)}>
                <option value="">{t('Let planner choose')}</option>{rules.filter(({ crop }) => !field.allowed_crops || field.allowed_crops.includes(crop))
                  .map(({ crop }) => <option key={crop} value={crop}>{t(crop)}</option>)}</select></label>)}</div></fieldset>
          </div>)}
        </details>
        </details>
        {!embedded && <div className="planner-generate"><button className="button primary" disabled={!selected.length || rules.length < 1} type="submit"><SparklesIcon aria-hidden="true" />{t(busy ? 'Planning…' : 'Generate and save draft')}</button></div>}
      </fieldset>
    </form>
    {error && <ErrorBox message={t(error)} />}
    {!embedded && draft && <PlannerResult draft={draft} />}
    {!embedded && application}

  </div>
}

function PlannerResult({ draft, focusedField, decisionFirst }: { draft: PlanningDraft; focusedField?: string; decisionFirst?: boolean }) {
  const { t, number, language } = usePlanningLocale()
  const result = draft.result
  const years = Array.from({ length: draft.season_count }, (_, index) => draft.start_season + index)
  const labels = { optimal: 'Best allocation within these rules', feasible: 'Feasible allocation', infeasible: 'Constraints need adjustment', search_limit: 'Search limit reached' }
  return <section className="planner-result" aria-labelledby="planner-result-title" aria-live="polite">
    <div className="planner-result-heading"><div><small>{draft.name} · {draft.start_season}–{draft.start_season + draft.season_count - 1}</small><h3 id="planner-result-title">{t(labels[result.status])}</h3></div>
      <span className="chip">{t(draft.applied_at ? 'Applied' : 'Draft')}</span></div>
    {result.diagnostics.length > 0 && <ul className="planner-diagnostics">{result.diagnostics.map((item, index) => <li key={index}>
      {item.field_id && <strong>{draft.fields.find(({ id }) => id === item.field_id)?.name} · </strong>}{item.crop && <strong>{t(item.crop)} · </strong>}{t(item.message)}
    </li>)}</ul>}
    {result.status === 'feasible' && <p className="planner-help">{t('The search stopped at its resource limit. This allocation satisfies all supplied constraints, but a better score may exist.')}</p>}
    {!!result.assignments.length && <>
      {decisionFirst && <><h4>{language === 'uk' ? `Вибір на сезон ${draft.start_season}` : `Choices for ${draft.start_season}`}</h4>
        <div className="strategy-decisions">{nextSeasonDecisions(draft, focusedField).map(({ field, assignment, focused }) => <article key={field.id} className="strategy-decision" data-focused={focused}>
          <strong>{field.name}</strong><span> · {number(field.area_ha)} {t('ha')}</span>
          <h4>{assignment ? t(assignment.crop) : t('Unknown')}</h4>
          {assignment?.locked && <p>{t('Locked')}</p>}
          {assignment && <><p>{language === 'uk' ? 'Чому цей розподіл' : 'Why this allocation'}</p><ul>{assignment.reasons.map(reason => <li key={reason}>{t(reason)}</li>)}</ul></>}
          <details><summary>{language === 'uk' ? 'Вікно сівби' : 'Sowing window'}</summary><p>{language === 'uk' ? 'Не визначено. Розподіл культур не містить календаря сівби. Потрібні регіональні строки, температура й вологість ґрунту, прогноз погоди та доступність поля. Оптимальний за погодою строк не оцінено.' : 'Not determined. Crop allocation does not include a sowing calendar. Regional dates, soil temperature and moisture, weather forecasts and field access are needed. Weather-optimal timing has not been assessed.'}</p></details>
          <p>{language === 'uk' ? 'Перед закупівлею: перевірте місцеву придатність і забезпечення технікою. Урожайність і маржа не розраховані.' : 'Before procurement: review local suitability and equipment. Yield and margin have not been calculated.'}</p>
        </article>)}</div></>}

      <h4>{t('Crop area by season')}</h4>
      <div className="planner-area-charts">{years.map((year) => <figure key={year}><figcaption>{year}</figcaption>
        <div className="planner-area-stack" role="img" aria-label={result.areas.filter((row) => row.season === year).map((row) => t(row.crop) + ' ' + number(row.area_ha) + ' ' + t('ha')).join(', ')}>
          {result.areas.filter((row) => row.season === year && row.area_ha > 0).map((row) => <span key={row.crop} style={{ width: row.share_percent + '%', background: colors[row.crop] }} />)}
        </div><ul>{result.areas.filter((row) => row.season === year).map((row) => <li key={row.crop}><i style={{ background: colors[row.crop] }} /><span>{t(row.crop)}</span><strong>{number(row.area_ha)} <small>{t('ha')}</small></strong><small>{number(row.share_percent, 0)}%</small></li>)}</ul>
      </figure>)}</div>
      <h4>{t('Crop sequence by field')}</h4><div className="planner-field-results">{draft.fields.map((field) => <article key={field.id}><header><strong>{field.name}</strong><small>{number(field.area_ha)} {t('ha')}</small></header>
        <div>{result.assignments.filter((row) => row.field_id === field.id).map((row) => <section key={row.season} style={{ '--crop-color': colors[row.crop] } as CSSProperties}>
          <span>{row.season}{row.locked && <LockClosedIcon aria-label={t('Locked')} />}</span><CropIcon crop={row.crop} /><strong>{t(row.crop)}</strong>
          <details><summary>{t('Constraints and calculation')}</summary><ul>{row.reasons.map((reason) => <li key={reason}>{t(reason)}</li>)}</ul></details>
        </section>)}</div>
      </article>)}</div>
    </>}
    {!!result.warnings.length && <ul className="planner-warnings">{result.warnings.map((item, index) => <li key={index}>
      <strong>{draft.fields.find(({ id }) => id === item.field_id)?.name} · </strong>{t(item.message)}</li>)}</ul>}
    <details className="planner-method"><summary>{t('Constraints and calculation')}</summary>
      <p>{language === 'uk' ? 'Оцінка — сума пріоритету культури × площа поля за обрані роки. Однакові оцінки розв’язуються детерміновано. Це не прогноз урожайності, прибутку чи водного ризику.' : 'The score sums crop preference × field area over the selected years. Equal scores are resolved deterministically. It is not a yield, profit or water-risk forecast.'}</p>
      <dl><div><dt>{t('Area-weighted preference')}</dt><dd>{result.objective === null ? '—' : number(result.objective)}</dd></div><div><dt>{t('Search steps')}</dt><dd>{number(result.explored_nodes, 0)} / {number(result.node_limit, 0)}</dd></div>
        {typeof result.generation_nodes === 'number' && <div><dt>{language === 'uk' ? 'Генерація кандидатів' : 'Candidate generation'}</dt><dd>{number(result.generation_nodes, 0)} / {number(result.generation_limit, 0)} · {language === 'uk' ? 'Повна' : 'Complete'}: {String(result.generation_complete)}</dd></div>}
      </dl>
      <ul className="planner-constraint-list">{draft.request.crops.map((rule) => <li key={rule.crop}><strong>{t(rule.crop)}</strong> · {t('Return break, years')} {rule.min_break_years} · {t('Preference, 0–10')} {rule.preference} · {t('Maximum share, %')} {number(rule.max_share_percent)} · {number(rule.min_area_ha)}–{rule.max_area_ha === null ? t('No limit') : number(rule.max_area_ha)} {t('ha')}</li>)}</ul>
      <p>{t('Unknown years are not assumed to be crop-free. Review local agronomic suitability, pesticide carry-over, pests and equipment before applying.')}</p>
      <a href="https://www.nrcs.usda.gov/resources/guides-and-instructions/conservation-crop-rotation-ac-328-conservation-practice-standard" target="_blank" rel="noreferrer">NRCS 328 · {t('Rotation principles')}</a>
    </details>
  </section>
}
