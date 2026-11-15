import { useCallback, useState } from 'react'
import { plantingOpportunitiesApi, type PlantingOpportunity } from '../../api/plantingOpportunities'
import type { Field } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { ErrorBox, Loading } from '../../shared/ui'
import { calendarPeriodLabel, canReviewAllocation, groupOpportunities, localPlanningDate, matchesOpportunityContext, validOpportunityPeriod } from './opportunities'

export function PlantingOpportunities({ field, busy, choose, openAnalytics, openProfiles }: {
  field: Field; busy: boolean; choose: (item: PlantingOpportunity) => void; openAnalytics: () => void; openProfiles: () => void
}) {
  const { language } = useLocale()
  const uk = language === 'uk'
  const [date, setDate] = useState(localPlanningDate)
  const [years, setYears] = useState(1)
  const valid = validOpportunityPeriod(date, years)
  const load = useCallback(async (signal: AbortSignal) => {
    if (!validOpportunityPeriod(date, years)) return null
    const data = await plantingOpportunitiesApi.get(field.id, date, years, signal)
    if (!matchesOpportunityContext(data, field.id, field.boundary_revision, date, years)) throw new Error(uk ? 'Календар не відповідає обраному полю, межам або періоду. Оновіть дані.' : 'Calendar does not match the selected field, boundary or period. Refresh the data.')
    return data
  }, [field.id, field.boundary_revision, date, years, uk])
  const resource = useResource(load)
  const data = resource.data && matchesOpportunityContext(resource.data, field.id, field.boundary_revision, date, years) ? resource.data : null
  const titles = { open: uk ? 'Перевірені вікна, відкриті на обрану дату' : 'Reviewed windows open on the planning date', current_reference_month: uk ? 'Поточний довідковий місяць' : 'Current reference month', upcoming: uk ? 'Найближчі календарні можливості' : 'Upcoming calendar opportunities', passed: uk ? 'Минулі вікна та довідкові місяці' : 'Passed windows and reference months', unknown: uk ? 'Календарні дані відсутні' : 'Missing calendar data' }
  return <section className="planting-opportunities" aria-labelledby="opportunities-title">
    <div className="strategies-purpose"><div><h2 id="opportunities-title">{uk ? 'Що можна розглянути для наступної сівби?' : 'What could you consider sowing next?'}</h2>
      <p>{uk ? 'Почніть з календаря для обраного поля. Порядок — за часом, не за прибутком. Регіональний типовий місяць не підтверджує придатність поля або оптимальний строк сівби.' : 'Start with the selected field’s calendar. Options are ordered by time, not profit. A typical regional month does not establish field suitability or the best sowing time.'}</p></div></div>
    <div className="strategies-context">
      <label>{uk ? 'Дата планування' : 'Planning date'}<input type="date" min="1900-01-01" max={`${2100 - years}-12-31`} value={date} onChange={e => setDate(e.target.value)} required /></label>
      <label>{uk ? 'Календарний горизонт, років' : 'Calendar lookahead, years'}<select value={years} onChange={e => setYears(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6].map(year => <option key={year} disabled={Number(date.slice(0, 4)) + year > 2100}>{year}</option>)}</select></label>
      <p>{uk ? 'Рухомий період від обраної дати. Це не кількість років урожаю в розподілі.' : 'A rolling period from the chosen date. This is separate from allocation harvest years.'}{data && <span>{data.planning_date} → {data.lookahead_end}</span>}</p>
    </div>
    {!valid && <p role="alert">{uk ? 'Укажіть дійсну дату від 1900 року та горизонт, що закінчується не пізніше 2100 року.' : 'Enter a valid planning date from 1900 with a lookahead ending by 2100.'}</p>}
    {valid && resource.pending && <Loading text={uk ? 'Завантажуємо календар поля…' : 'Loading the field calendar…'} />}
    {valid && resource.error && <ErrorBox message={resource.error} retry={resource.reload} />}
    {data && !resource.pending && <>
      <p className="strategies-calendar-note">{uk ? 'Погода для сівби та прибуток не оцінені. Застосування календарної ідеї лише відкриє розподіл на відповідний рік урожаю; культуру ще потрібно обрати й перевірити.' : 'Sowing weather and profit are not assessed. Reviewing a calendar idea opens allocation for its harvest year; crop choice and suitability still need review.'}</p>
      {data.items.length === 0 && <p role="status">{uk ? 'Для цього поля й періоду немає календарних записів. Це не означає, що сівба неможлива.' : 'No calendar entries are available for this field and period. This does not establish that sowing is impossible.'}</p>}
      {groupOpportunities(data.items).map(group => group.items.length > 0 && <OpportunityGroup key={`${field.id}:${date}:${years}:${group.key}`} title={titles[group.key]} collapsed={group.key === 'passed' || group.key === 'unknown'} items={group.items} busy={busy} choose={choose} />)}
      <details className="opportunity-method"><summary>{uk ? 'Джерела, межі та відсутні дані' : 'Sources, limitations and missing inputs'}</summary>
        <p>{uk ? 'Стан профілю' : 'Profile status'}: {data.profile_status} · {uk ? 'Версія меж' : 'Boundary revision'} {data.geometry_revision} · {uk ? 'Версія профілю' : 'Profile revision'} {data.profile_revision ?? '—'}</p>
        <p>{uk ? 'Дані відомі станом на' : 'Knowledge as of'}: {data.knowledge_as_of}; {uk ? 'відповідь створено' : 'response generated'}: {data.generated_at}</p>
        <ul>{data.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>
        <p>{uk ? 'Для оцінки потрібні відповідні календарі, профіль ґрунту, погодні дані та обмеження культури. Саме створення профілю не забезпечує цих джерел.' : 'Assessment needs relevant calendars, a soil profile, weather data and crop constraints. Creating a profile alone does not supply these sources.'}</p>
      </details>
    </>}
    <div className="opportunity-actions"><button className="button" onClick={openProfiles}>{uk ? 'Переглянути дані для оцінки' : 'Review assessment inputs'}</button><button className="button" onClick={openAnalytics}>{uk ? 'Переглянути водні умови' : 'Review water conditions'}</button></div>
  </section>
}

function OpportunityGroup({ title, collapsed, items, busy, choose }: { title: string; collapsed: boolean; items: PlantingOpportunity[]; busy: boolean; choose: (item: PlantingOpportunity) => void }) {
  const { language } = useLocale()
  const [all, setAll] = useState(false)
  const content = <><div className="opportunity-grid">{(all ? items : items.slice(0, 6)).map(item => <OpportunityCard key={item.id} item={item} busy={busy} choose={choose} />)}</div>
    {items.length > 6 && <button className="button" aria-expanded={all} onClick={() => setAll(!all)}>{all ? (language === 'uk' ? 'Показати менше' : 'Show fewer') : (language === 'uk' ? `Показати всі (${items.length})` : `Show all (${items.length})`)}</button>}</>
  return collapsed ? <details className="opportunity-group"><summary>{title} ({items.length})</summary>{content}</details>
    : <section className="opportunity-group"><h3>{title} <span>({items.length})</span></h3>{content}</section>
}
function OpportunityCard({ item, busy, choose }: { item: PlantingOpportunity; busy: boolean; choose: (item: PlantingOpportunity) => void }) {
  const { language, crop } = useLocale()
  const uk = language === 'uk'
  const planting = calendarPeriodLabel(item.planting_period, item.date_precision, uk ? 'uk-UA' : 'en-GB')
  const maturity = calendarPeriodLabel(item.maturity_period, item.date_precision, uk ? 'uk-UA' : 'en-GB')
  const kind = item.calendar_kind === 'reviewed_window' ? (uk ? 'Перевірене календарне вікно' : 'Reviewed calendar window') : item.calendar_kind === 'regional_typical_month' ? (uk ? 'Типовий регіональний місяць' : 'Typical regional month') : (uk ? 'Календар невідомий' : 'Calendar unknown')
  const canChoose = canReviewAllocation(item)
  const missingLabels: Record<string, string> = uk ? {
    crop_calendar: 'Календар культури', field_weather: 'Погода для поля', soil_workability: 'Готовність ґрунту до обробітку', preceding_crop_harvest: 'Дата збирання попередньої культури', cultivar_maturity: 'Строк дозрівання сорту', economics: 'Витрати та очікувані ціни', current_field_profile: 'Актуальний паспорт поля', arable_land_evidence: 'Підтвердження орної землі', soil_profile: 'Профіль ґрунту',
  } : {
    crop_calendar: 'Crop calendar', field_weather: 'Field weather', soil_workability: 'Soil workability', preceding_crop_harvest: 'Preceding crop harvest date', cultivar_maturity: 'Cultivar maturity', economics: 'Costs and expected prices', current_field_profile: 'Current field profile', arable_land_evidence: 'Arable land evidence', soil_profile: 'Soil profile',
  }
  const unavailableReason = !item.allocation_supported ? (uk ? 'Культура не підтримується розподілом.' : 'This crop is not supported by allocation.')
    : item.calendar_status === 'passed' ? (uk ? 'Цей календарний запис уже минув. Перегляньте наступний цикл.' : 'This calendar entry has passed. Review the next cycle.')
      : item.eligibility === 'ineligible' ? (uk ? 'Джерела вказують на непридатність. Перегляньте обґрунтування.' : 'Sources mark this entry ineligible. Review the reasons.')
        : item.harvest_year === null ? (uk ? 'Рік урожаю невідомий; його не підставлено за припущенням.' : 'The harvest year is unknown; no assumed year is supplied.')
          : (uk ? 'Немає доступного переходу до нового розподілу: перевірте календар та підтримуваний рік урожаю (2001–2100).' : 'A new allocation is unavailable: review the calendar and supported harvest year (2001–2100).')
  return <article className="opportunity-card" data-opportunity-id={item.id}>
    <p className="opportunity-kind">{kind}</p><h4>{crop(item.crop_id)}</h4>
    <p className="opportunity-cycle">{uk ? 'Цикл' : 'Cycle'}: {item.cycle_id ?? (uk ? 'невідомий' : 'unknown')} · {item.system}</p>
    <dl><dt>{item.date_precision === 'month' ? (uk ? 'Довідковий місяць сівби' : 'Reference planting month') : (uk ? 'Календарне вікно сівби' : 'Calendar planting window')}</dt><dd>{planting ?? (uk ? 'Не визначено' : 'Not determined')}</dd>
      <dt>{uk ? 'Дозрівання / кінець росту' : 'Maturity / growth end'}</dt><dd>{maturity ?? (uk ? 'Не визначено' : 'Not determined')}</dd>
      <dt>{uk ? 'Рік урожаю для розподілу' : 'Allocation harvest year'}</dt><dd>{item.harvest_year ?? (uk ? 'Невідомий' : 'Unknown')}</dd></dl>
    {item.date_precision === 'month' && <p>{uk ? 'Місяць — регіональний орієнтир, не інтервал дозволених дат.' : 'The month is a regional reference, not a range of admissible dates.'}</p>}
    <p>{uk ? 'Придатність' : 'Eligibility'}: {({ review_required: uk ? 'потрібна перевірка' : 'review required', not_assessed: uk ? 'не оцінено' : 'not assessed', ineligible: uk ? 'непридатно за оцінкою' : 'ineligible', unavailable: uk ? 'дані недоступні' : 'unavailable' })[item.eligibility]}.</p>
    {canChoose ? <button className="button primary" disabled={busy} onClick={() => choose(item)}>{uk ? `Розглянути розподіл на ${item.harvest_year}` : `Review allocation for harvest ${item.harvest_year}`}</button>
      : <p className="opportunity-unavailable">{unavailableReason}</p>}
    <details><summary>{uk ? 'Чому цей запис і звідки дані?' : 'Why this entry, and where is it from?'}</summary>
      <p>{uk ? 'Підстава року урожаю' : 'Harvest-year basis'}: {({ reviewed_growth_window_end: uk ? 'кінець перевіреного вікна росту' : 'reviewed growth-window end', regional_maturity_month: uk ? 'регіональний місяць дозрівання' : 'regional maturity month', unknown: uk ? 'невідомо' : 'unknown' })[item.maturity_basis]}</p>
      <p>{uk ? 'Оцінку моделлю не виконано. Наявність профілю культури не підтверджує точність для поля.' : 'Model evaluation has not run. A crop profile does not establish accuracy for this field.'} {uk ? 'Профіль культури' : 'Crop profile'}: {item.native_crop_profile_available ? (uk ? 'наявний' : 'available') : (uk ? 'відсутній' : 'unavailable')}.</p>
      <p>{uk ? 'Погода для сівби: не оцінена. Прибуток: не оцінений.' : 'Sowing weather: not assessed. Profit: not assessed.'}</p>
      {item.missing_data.length > 0 && <><strong>{uk ? 'Бракує даних' : 'Missing data'}</strong><ul>{item.missing_data.map((reason, index) => <li key={index}>{missingLabels[reason] ?? reason}</li>)}</ul></>}
      <ul>{item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
      {item.source ? <div className="opportunity-source"><p>{uk ? 'Версія джерела' : 'Source version'}: {item.source.version}</p><a href={/^https?:\/\//.test(item.source.source_url) ? item.source.source_url : undefined} target="_blank" rel="noreferrer">{uk ? 'Джерело календаря' : 'Calendar source'}</a>
        <p>{uk ? 'Ліцензія' : 'License'}: {item.source.license ?? (uk ? 'не вказана' : 'not supplied')} · {uk ? 'Доступне з' : 'Available at'}: {item.source.available_at}</p>
        {item.source.reference_year !== null && <p>{uk ? 'Довідковий рік' : 'Reference year'}: {item.source.reference_year}</p>}
        {item.source.resolution_arc_minutes !== null && <p>{uk ? 'Розмір сітки, кутових хвилин' : 'Grid resolution, arc minutes'}: {item.source.resolution_arc_minutes} · {item.source.grid_latitude}, {item.source.grid_longitude}</p>}
        {item.source.checksum_sha256 && <p>SHA256: {item.source.checksum_sha256}</p>}</div> : <p>{uk ? 'Джерело календаря відсутнє.' : 'No calendar source is available.'}</p>}
    </details>
  </article>
}
