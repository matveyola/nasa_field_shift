import { useEffect, useState } from 'react'
import type { StrategiesPageProps } from '../../shared/productContext'
import { useLocale } from '../../shared/locale'
import { FarmRotationPlanner } from '../rotations/FarmRotationPlanner'
import { PlanningPage } from '../planning-integration/PlanningPage'
import { allocationSeasonSupported, initialStrategyView } from './model'
import './strategies.css'

export function StrategiesPage({ field, fields, season, trajectory, focus, onContext, openAnalytics, openAgent }: StrategiesPageProps) {
  const { language } = useLocale()
  const uk = language === 'uk'
  const [view, setView] = useState(() => initialStrategyView(focus))
  const [applied, setApplied] = useState(false)
  // The inspection season can change in a result matrix; only the explicit period control starts a new allocation.
  const [firstSeason, setFirstSeason] = useState(season)
  const [seasonCount, setSeasonCount] = useState(1)
  useEffect(() => { if (focus) setView('scenarios') }, [focus])
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
      {view === 'allocation' && <label>{uk ? 'Перший рік урожаю' : 'First harvest year'}<select value={firstSeason} onChange={e => {
        const year = Number(e.target.value)
        setFirstSeason(year); onContext(field.id, year, trajectory ?? '')
      }}>
        {!allocationSeasonSupported(firstSeason) && <option value={firstSeason} disabled>{firstSeason}</option>}
        {Array.from({ length: 101 - seasonCount }, (_, index) => 2001 + index).map(year => <option key={year} value={year}>{year}</option>)}
      </select></label>}
      <p><strong>{view === 'allocation' ? `${firstSeason} → ${firstSeason + seasonCount - 1}` : season}</strong><span>{uk ? 'План для господарства · обране поле у фокусі' : 'Farm plan · selected field in focus'}</span></p>
    </div>
    <p className="strategies-calendar-note">{uk ? 'Рік плану — рік урожаю. Озимі можуть висіватися попереднього року. Розподіл передбачає одну культуру на полі за рік.' : 'A planning year is the harvest year. Winter crops may be sown in the previous year. Allocation uses one crop per field per year.'}</p>
    <div className="strategies-workflows" role="group" aria-label={uk ? 'Спосіб планування' : 'Planning workflow'}>
      <button className="strategies-workflow" aria-pressed={view === 'allocation'} onClick={() => setView('allocation')}>
        <strong>{uk ? 'Розподіл культур' : 'Crop allocation'}</strong><span>{uk ? 'Від 1 до 6 років · площі та ваші обмеження' : '1–6 years · areas and your constraints'}</span></button>
      <button className="strategies-workflow" aria-pressed={view === 'scenarios'} onClick={() => setView('scenarios')}>
        <strong>{uk ? 'Порівняння сценаріїв' : 'Scenario comparison'}</strong><span>{uk ? '1–3 роки · за наявності перевірених вхідних даних' : '1–3 years · with reviewed model inputs'}</span></button>
    </div>
    <div hidden={view !== 'allocation'}>
      <div className="strategies-purpose"><div><h2>{uk ? 'Сплануйте культури, потім закупівлі' : 'Plan crops, then procurement'}</h2>
        <p>{uk ? 'Ціль: розподілити цілі поля за вашими пріоритетами культур, площами та перервами сівозміни. Це план розміщення, а не прогноз урожайності, прибутку чи погодних ризиків.' : 'Goal: allocate whole fields using your crop preferences, area limits and rotation breaks. This is an allocation plan, not a yield, profit or weather-risk forecast.'}</p></div>
        <button className="button" onClick={() => openAnalytics('rotation')}>{uk ? 'Перевірити історію полів' : 'Review field history'}</button></div>
      {applied && <p role="status" className="strategies-notice">{uk ? 'Культури збережено для обраних полів. План можна переглянути у збережених.' : 'Crops saved for the selected fields. Reopen the plan from saved plans.'}</p>}
      {allocationSeasonSupported(firstSeason) ? <FarmRotationPlanner season={firstSeason} seasonCount={seasonCount} onSeasonCountChange={setSeasonCount} embedded focusedField={field.id} onApplied={() => setApplied(true)} />
        : <p role="status">{uk ? 'Розподіл доступний для першого року урожаю 2001–2100. Оберіть інший рік.' : 'Allocation supports first harvest years 2001–2100. Choose another year.'}</p>}
    </div>
    <div hidden={view !== 'scenarios'}>
      <PlanningPage fieldId={field.id} season={season} trajectory={trajectory} onContext={onContext} embedded fieldNames={names} focus={focus} />
    </div>
    <aside className="strategies-next"><p>{uk ? 'Перед підтвердженням перевірте придатність культур, техніку й актуальність історії. Для строків сівби перевірте календар і водні умови. Зони та норми внесення залишаються у робочому просторі поля.' : 'Before confirming, review crop suitability, equipment and history freshness. For sowing timing, review the calendar and water conditions. Zones and application rates remain in the field workspace.'}</p>
      <button className="button" onClick={() => openAnalytics('water')}>{uk ? 'Переглянути водні умови' : 'Review water conditions'}</button></aside>
  </section>
}
