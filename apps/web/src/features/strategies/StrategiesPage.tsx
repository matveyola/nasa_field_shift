import { useEffect, useRef, useState } from 'react'
import type { StrategiesPageProps } from '../../shared/productContext'
import { useLocale } from '../../shared/locale'
import { FarmRotationPlanner, type AllocationDraftState } from '../rotations/FarmRotationPlanner'
import { PlanningPage } from '../planning-integration/PlanningPage'
import { allocationSeasonSupported, initialStrategyView } from './model'
import { PlantingOpportunities } from './PlantingOpportunities'
import type { PlantingOpportunity } from '../../api/plantingOpportunities'
import { canReviewAllocation } from './opportunities'
import './strategies.css'

export function StrategiesPage({ field, fields, season, trajectory, focus, onContext, openAnalytics, openAgent }: StrategiesPageProps) {
  const { language } = useLocale()
  const uk = language === 'uk'
  const [view, setView] = useState<'opportunities' | 'allocation' | 'scenarios'>(() => initialStrategyView(focus))
  const [applied, setApplied] = useState(false)
  // The inspection season can change in a result matrix; only the explicit period control starts a new allocation.
  const [firstSeason, setFirstSeason] = useState(season)
  const [seasonCount, setSeasonCount] = useState(1)
  const [draftState, setDraftState] = useState<AllocationDraftState>({ dirty: false, busy: false })
  const [calendarChoice, setCalendarChoice] = useState<PlantingOpportunity | null>(null)
  const [pendingPeriod, setPendingPeriod] = useState<{ year: number; item: PlantingOpportunity | null } | null>(null)
  const [inputFocus, setInputFocus] = useState<'profiles' | 'economics' | undefined>(focus)
  const confirmation = useRef<HTMLDialogElement>(null)
  useEffect(() => { setPendingPeriod(null); setCalendarChoice(null) }, [field.id, field.boundary_revision])
  useEffect(() => {
    if (pendingPeriod) confirmation.current?.showModal()
    else confirmation.current?.close()
  }, [pendingPeriod])
  function openPeriod(year: number, item: PlantingOpportunity | null) {
    setFirstSeason(year)
    if (year + seasonCount - 1 > 2100) setSeasonCount(1)
    setDraftState({ dirty: false, busy: false })
    setCalendarChoice(item); setPendingPeriod(null); setView('allocation')
    onContext(field.id, year, trajectory ?? '')
  }
  function requestPeriod(year: number, item: PlantingOpportunity | null = null) {
    if (draftState.busy || !allocationSeasonSupported(year) || (item && !canReviewAllocation(item))) return
    if (year === firstSeason) { setCalendarChoice(item); setView('allocation'); return }
    if (draftState.dirty || year + seasonCount - 1 > 2100) setPendingPeriod({ year, item })
    else openPeriod(year, item)
  }
  useEffect(() => { setInputFocus(focus); if (focus) setView('scenarios') }, [focus])
  useEffect(() => { setApplied(false) }, [firstSeason, seasonCount])
  const names = Object.fromEntries(fields.map(f => [f.id, f.name]))
  return <section className="strategies-page" aria-labelledby="strategies-title">
    <header className="strategies-heading"><div><p className="strategies-eyebrow">{uk ? 'РІШЕННЯ ДО ЗАКУПІВЕЛЬ' : 'DECISIONS BEFORE PROCUREMENT'}</p>
      <h1 id="strategies-title">{uk ? 'Що сіяти наступним?' : 'What to sow next?'}</h1>
      <p>{uk ? 'Оберіть культури та період планування до закупівлі насіння й ресурсів.' : 'Choose crops and a planning period before buying seed and resources.'}</p></div>
      <button className="button" onClick={openAgent}>{uk ? 'Обговорити з асистентом' : 'Discuss with assistant'}</button>
    </header>
    <div className="strategies-context">
      <label>{uk ? 'Поле у фокусі' : 'Field in focus'}<select value={field.id} onChange={e => onContext(e.target.value, season, trajectory ?? '')}>
        {fields.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      {view === 'allocation' && <label>{uk ? 'Перший рік урожаю' : 'First harvest year'}<select value={firstSeason} disabled={draftState.busy} onChange={e => requestPeriod(Number(e.target.value))}>
        {!allocationSeasonSupported(firstSeason) && <option value={firstSeason} disabled>{firstSeason}</option>}
        {Array.from({ length: 101 - seasonCount }, (_, index) => 2001 + index).map(year => <option key={year} value={year}>{year}</option>)}
      </select></label>}
      {view !== 'opportunities' && <p><strong>{view === 'allocation' ? `${firstSeason} → ${firstSeason + seasonCount - 1}` : season}</strong><span>{uk ? 'План для господарства · обране поле у фокусі' : 'Farm plan · selected field in focus'}</span></p>}
    </div>
    {view !== 'opportunities' && <p className="strategies-calendar-note">{uk ? 'Рік плану — рік урожаю. Озимі можуть висіватися попереднього року. Розподіл передбачає одну культуру на полі за рік.' : 'A planning year is the harvest year. Winter crops may be sown in the previous year. Allocation uses one crop per field per year.'}</p>}
    <div className="strategies-workflows" role="group" aria-label={uk ? 'Спосіб планування' : 'Planning workflow'}>
      <button className="strategies-workflow" aria-pressed={view === 'opportunities'} onClick={() => setView('opportunities')}>
        <strong>{uk ? 'Найближча сівба' : 'Next sowing opportunities'}</strong><span>{uk ? 'Календар поля · від обраної дати' : 'Field calendar · from your planning date'}</span></button>
      <button className="strategies-workflow" aria-pressed={view === 'allocation'} onClick={() => setView('allocation')}>
        <strong>{uk ? 'Розподіл культур' : 'Crop allocation'}</strong><span>{uk ? 'Від 1 до 6 років · площі та ваші обмеження' : '1–6 years · areas and your constraints'}</span></button>
      <button className="strategies-workflow" aria-pressed={view === 'scenarios'} onClick={() => setView('scenarios')}>
        <strong>{uk ? 'Порівняння сценаріїв' : 'Scenario comparison'}</strong><span>{uk ? '1–3 роки · за наявності перевірених вхідних даних' : '1–3 years · with reviewed model inputs'}</span></button>
    </div>
    <div hidden={view !== 'opportunities'}>
      <PlantingOpportunities field={field} busy={draftState.busy} choose={item => { if (item.harvest_year !== null) requestPeriod(item.harvest_year, item) }} openAnalytics={() => openAnalytics('water')} openProfiles={() => { setInputFocus('profiles'); setView('scenarios') }} />
    </div>
    <dialog ref={confirmation} className="opportunity-confirm" aria-labelledby="replace-allocation-title" onCancel={() => setPendingPeriod(null)}>
      <h2 id="replace-allocation-title">{uk ? 'Змінити період чернетки?' : 'Replace the draft period?'}</h2>
      <p>{uk ? `Перехід до урожаю ${pendingPeriod?.year ?? ''} скине поточні незбережені зміни, обмеження та вибрану чернетку розподілу. Збережені плани залишаться доступними. Культури на полях не зміняться.` : `Moving to harvest ${pendingPeriod?.year ?? ''} resets current unsaved edits, constraints and the selected allocation draft. Saved plans remain available. Field crops will not change.`}</p>
      {pendingPeriod && pendingPeriod.year + seasonCount - 1 > 2100 && <p>{uk ? `Поточні ${seasonCount} років виходять за межі 2100 року. Підтвердження встановить один рік урожаю: ${pendingPeriod.year}.` : `The current ${seasonCount} years exceed the 2100 limit. Confirming changes the allocation to one harvest year: ${pendingPeriod.year}.`}</p>}
      <div className="opportunity-actions"><button className="button" onClick={() => setPendingPeriod(null)} autoFocus>{uk ? 'Зберегти поточну чернетку' : 'Keep current draft'}</button>
        <button className="button primary" disabled={draftState.busy} onClick={() => { if (pendingPeriod) openPeriod(pendingPeriod.year, pendingPeriod.item) }}>{uk ? 'Перейти до нового періоду' : 'Start the new period'}</button></div>
    </dialog>
    <div hidden={view !== 'allocation'}>
      {calendarChoice && <p role="status" className="strategies-notice">{uk ? `Календарна ідея: ${calendarChoice.crop_id}, цикл ${calendarChoice.cycle_id ?? '—'}, урожай ${calendarChoice.harvest_year}. Змінено лише рік плану. Культуру не зафіксовано; перевірте обмеження нижче.` : `Calendar idea: ${calendarChoice.crop_id}, cycle ${calendarChoice.cycle_id ?? '—'}, harvest ${calendarChoice.harvest_year}. Only the planning year is prefilled. No crop is locked; review constraints below.`}</p>}
      <div className="strategies-purpose"><div><h2>{uk ? 'Сплануйте культури, потім закупівлі' : 'Plan crops, then procurement'}</h2>
        <p>{uk ? 'Ціль: розподілити цілі поля за вашими пріоритетами культур, площами та перервами сівозміни. Це план розміщення, а не прогноз урожайності, прибутку чи погодних ризиків.' : 'Goal: allocate whole fields using your crop preferences, area limits and rotation breaks. This is an allocation plan, not a yield, profit or weather-risk forecast.'}</p></div>
        <button className="button" onClick={() => openAnalytics('rotation')}>{uk ? 'Перевірити історію полів' : 'Review field history'}</button></div>
      {applied && <p role="status" className="strategies-notice">{uk ? 'Культури збережено для обраних полів. План можна переглянути у збережених.' : 'Crops saved for the selected fields. Reopen the plan from saved plans.'}</p>}
      {allocationSeasonSupported(firstSeason) ? <FarmRotationPlanner season={firstSeason} seasonCount={seasonCount} onSeasonCountChange={setSeasonCount} embedded focusedField={field.id} onApplied={() => setApplied(true)} onDraftState={setDraftState} />
        : <p role="status">{uk ? 'Розподіл доступний для першого року урожаю 2001–2100. Оберіть інший рік.' : 'Allocation supports first harvest years 2001–2100. Choose another year.'}</p>}
    </div>
    <div hidden={view !== 'scenarios'}>
      <PlanningPage fieldId={field.id} season={season} trajectory={trajectory} onContext={onContext} embedded fieldNames={names} focus={inputFocus} />
    </div>
    {view !== 'opportunities' && <aside className="strategies-next"><p>{uk ? 'Перед підтвердженням перевірте придатність культур, техніку й актуальність історії. Для строків сівби перевірте календар і водні умови. Зони та норми внесення залишаються у робочому просторі поля.' : 'Before confirming, review crop suitability, equipment and history freshness. For sowing timing, review the calendar and water conditions. Zones and application rates remain in the field workspace.'}</p>
      <button className="button" onClick={() => openAnalytics('water')}>{uk ? 'Переглянути водні умови' : 'Review water conditions'}</button></aside>}
  </section>
}
