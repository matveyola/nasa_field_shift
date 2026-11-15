import { useState } from 'react'
import type { HandoffCatalog, HandoffResult } from '../../../api/rotationHandoff'
import { useLocale } from '../../../shared/locale'
import { array, numeric, object, rankedRecommendations, string, strings } from './model'
import { cropNames, label, priorityLabels } from './copy'
import { canUseRanking, weatherBasis, weatherLabels } from './brief'
import { DecisionBrief } from './DecisionBrief'

export function HandoffResults({ result, catalog, reviewInputs, openAgent }: { result: HandoffResult; catalog?: HandoffCatalog; reviewInputs?: () => void; openAgent?: () => void }) {
  const { language } = useLocale(); const uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const payload = result.payload
  const plans = array(payload.plans).map(object)
  const decision = object(payload.decision)
  const rankingUsable = canUseRanking(result)
  const diagnostics = array(object(result).diagnostics).map(object)
  const recommendations = result.status === 'available' && result.operation === 'forecast' ? rankedRecommendations(payload, rankingUsable) : []
  const priorities = strings(decision.priorities_used)
  const weather = object(payload.weather)
  return <section className="handoff-results" aria-labelledby="handoff-results-title">
    <p className="handoff-badge">{text('Experimental · field accuracy not validated', 'Експериментально · польова точність не перевірена')}</p>
    <h3 id="handoff-results-title">{result.status === 'available' ? text('Your calculated comparison', 'Ваше розраховане порівняння') : text('The model could not assess these plans', 'Модель не змогла оцінити ці плани')}</h3>
    <p>{text('Data basis', 'Основа даних')}: {({ historical_replay: text('Historical replay', 'Історичне відтворення'), hybrid_scenario: weatherLabels[weatherBasis(result)][uk ? 1 : 0], conditional_climate: text('Conditional climate scenario, not an issued weather forecast', 'Умовний кліматичний сценарій, не оперативний прогноз погоди'), supplied_balance: text('Supplied water balance', 'Наданий водний баланс') })[result.data_kind]}</p>
    {string(payload.as_of) && <p>{text('Calculation cutoff', 'Дата відсікання розрахунку')}: {string(payload.as_of)}</p>}
    <p>{text('Suitability evidence', 'Докази придатності')}: {({ not_assessed: text('Not assessed', 'Не оцінено'), arable_land_only: text('Arable land evidence only; crop suitability is not validated', 'Лише підтвердження орної землі; придатність культури не перевірена'), review_required: text('Review required', 'Потрібна перевірка'), no_thermal_growth: text('No thermal growth in the assessed period', 'Немає теплових умов росту в оціненому періоді') } as Record<string, string>)[string(object(result).suitability_status) ?? 'not_assessed'] ?? text('Not assessed', 'Не оцінено')}</p>
    {diagnostics.length > 0 && <ul className="handoff-diagnostics">{diagnostics.map((item, index) => <li key={index}><strong>{string(item.severity)}</strong>: {string(item.message) ?? string(item.code)}{string(item.crop_id) ? ` · ${item.crop_id}` : ''}{string(item.start_date) ? ` · ${item.start_date} → ${item.end_date ?? '—'}` : ''}</li>)}</ul>}
    {result.reasons.length > 0 && <ul>{result.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>}
    <DecisionBrief result={result} reviewInputs={reviewInputs} openAgent={openAgent} />
    {result.status === 'available' && result.operation === 'forecast' && <>
      {recommendations.length > 0 ? <div className="handoff-ranking"><strong>{text('First by the selected criterion', 'Перші за обраним критерієм')}: {recommendations.map(row => planName(row.id, uk)).join(', ')}</strong><p>{priorities.map(priority => label(priorityLabels, priority, uk)).join(', ')}. {text('This rank comes from the engine among plans passing its resource checks. It does not validate local crop suitability or guarantee an outcome.', 'Це ранг моделі серед планів, що пройшли її перевірки ресурсів. Він не підтверджує місцеву придатність культури й не гарантує результат.')}</p></div>
        : <p className="strategies-notice">{text('No recommended plan is established by this result. Review missing inputs, resource checks and comparable metrics.', 'Цей результат не визначає рекомендованого плану. Перевірте відсутні дані, ресурси й порівнюваність показників.')}</p>}
      {strings(decision.priorities_missing).length > 0 && <p>{text('Cannot rank on', 'Неможливо ранжувати за')}: {strings(decision.priorities_missing).map(priority => label(priorityLabels, priority, uk)).join(', ')}</p>}
      {weather.interpretation === 'conditional_historical_scenarios' && <p>{text('Scenario spread describes the sampled historical weather sequences. It is not a calibrated probability or prediction interval.', 'Розкид сценаріїв описує вибрані історичні послідовності погоди. Це не калібрована ймовірність або інтервал прогнозу.')}</p>}
      <div className="handoff-outcomes">{plans.map((plan, i) => <PlanOutcome key={`${result.evidence_sha256}:${string(plan.id) ?? i}`} plan={plan} decision={array(decision.decisions).map(object).find(row => row.id === plan.id) ?? {}} catalog={catalog} rankingUsable={rankingUsable} />)}</div>
      {plans.length === 0 && <p>{text('No plan metrics were returned.', 'Показники планів не повернуто.')}</p>}
    </>}
    <details className="handoff-provenance"><summary>{text('Sources and interpretation', 'Джерела та інтерпретація')}</summary>
      <p>{text('Engine', 'Рушій')}: {result.engine_version} · {text('Source revision', 'Версія вихідного коду')}: {result.source_commit}</p>
      <p>{text('Acquired at', 'Отримано')}: {result.acquired_at}</p><p>{text('Input checksum', 'Контрольна сума входу')}: {result.input_sha256}</p><p>{text('Evidence checksum', 'Контрольна сума доказів')}: {result.evidence_sha256}</p>
      {string(payload.soil_source_url) && <SourceLink url={string(payload.soil_source_url)!}>{text('Soil source', 'Джерело ґрунту')}</SourceLink>}
      {string(weather.live_source_url) && <SourceLink url={string(weather.live_source_url)!}>{text('Short weather forecast source', 'Джерело короткого прогнозу погоди')}</SourceLink>}
      {string(weather.historical_source_url) && <SourceLink url={string(weather.historical_source_url)!}>{text('Historical weather source', 'Джерело історичної погоди')}</SourceLink>}
      <ul>{result.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
      {result.source_receipts.length ? <ul className="handoff-receipts">{result.source_receipts.map((receipt, i) => <li key={i}><SourceLink url={receipt.source_url}>{receipt.source_url}</SourceLink><p>{receipt.kind} · {receipt.available_at} · {receipt.bytes} bytes</p><p>SHA256: {receipt.sha256}</p></li>)}</ul> : <p>{text('No source receipts were returned.', 'Квитанції джерел не повернуто.')}</p>}
      <details><summary>{text('Exact saved model output', 'Точний збережений результат моделі')}</summary><pre>{JSON.stringify(result.payload, null, 2)}</pre></details>
    </details>
  </section>
}

function PlanOutcome({ plan, decision, catalog, rankingUsable }: { rankingUsable: boolean; plan: Record<string, unknown>; decision: Record<string, unknown>; catalog?: HandoffCatalog }) {
  const { language } = useLocale(); const uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const [memberIndex, setMemberIndex] = useState(0)
  const members = array(plan.members).map(object)
  const member = members[memberIndex] ?? members[0] ?? {}
  const economics = object(plan.economics)
  const format = (value: unknown, unit = '') => numeric(value) === null ? text('Unknown', 'Невідомо') : `${new Intl.NumberFormat(uk ? 'uk-UA' : 'en-GB', { maximumFractionDigits: 2 }).format(numeric(value)!)}${unit ? ` ${unit}` : ''}`
  const money = (value: unknown, currency: unknown) => typeof currency === 'string' && /^[A-Z]{3}$/.test(currency) ? format(value, `${currency}/ha`) : text('Unknown currency or amount', 'Невідома валюта або сума')
  const feasible = string(decision.feasibility)
  const rank = numeric(decision.rank)
  return <article className="handoff-outcome" data-handoff-plan={string(plan.id) ?? ''}>
    <h4>{planName(plan.id, uk)}{rankingUsable && feasible === 'feasible' && rank !== null && <span> · {text('Rank', 'Ранг')} {rank}</span>}</h4>
    <p>{array(member.seasons).map(value => { const cropId = string(object(value).crop_id) ?? ''; return uk ? catalog?.crops.find(crop => crop.id === cropId)?.name ?? cropId : cropNames[cropId] ?? cropId }).join(' → ')}</p>
    {strings(decision.blockers).length > 0 && <p>{text('Why blocked', 'Чому заблоковано')}: {strings(decision.blockers).join('; ')}</p>}
    {strings(decision.unknowns).length > 0 && <p>{text('What needs confirmation', 'Що слід підтвердити')}: {strings(decision.unknowns).join('; ')}</p>}
    <p>{text('Resource feasibility', 'Здійсненність за ресурсами')}: {feasible === 'feasible' ? text('Passes supplied resource checks', 'Проходить перевірки наданих ресурсів') : feasible === 'infeasible' ? text('Blocked', 'Заблоковано') : feasible === 'needs_confirmation' ? text('Needs confirmation', 'Потребує підтвердження') : text('Not assessed', 'Не оцінено')}</p>
    <dl className="handoff-metrics"><div><dt>{text('Median water shortfall', 'Медіанний дефіцит води')}</dt><dd>{format(plan.median_pct, '%')}</dd></div><div><dt>{text('Tested shortfall range', 'Діапазон дефіциту у сценаріях')}</dt><dd>{format(plan.minimum_pct, '%')} – {format(plan.maximum_pct, '%')}</dd></div><div><dt>{text('Median water-stress days', 'Медіана частки днів водного стресу')}</dt><dd>{format(plan.median_water_stress_days_pct, '%')}</dd></div><div><dt>{text('Scheduled net irrigation', 'Запланований полив нетто')}</dt><dd>{format(plan.scheduled_net_irrigation_mm, 'mm')}</dd></div></dl>
    <details><summary>{text('Model checks and gaps', 'Перевірки моделі та прогалини')}</summary>
      {array(decision.blockers).length > 0 && <><strong>{text('Blocking checks', 'Перешкоди')}</strong><ul>{strings(decision.blockers).map((item, i) => <li key={i}>{item}</li>)}</ul></>}
      {array(decision.unknowns).length > 0 && <><strong>{text('Unconfirmed inputs', 'Непідтверджені дані')}</strong><ul>{strings(decision.unknowns).map((item, i) => <li key={i}>{item}</li>)}</ul></>}
      <dl>{Object.entries(object(decision.values)).map(([key, value]) => <div key={key}><dt>{label(priorityLabels, key, uk)}</dt><dd>{format(value, ({ water_stress: '% days', water_deficit: '%', water_saving: 'mm', yield: 't/ha', soil_cover: 'days', revenue: typeof decision.gross_revenue_currency === 'string' ? `${decision.gross_revenue_currency}/ha` : '' } as Record<string, string>)[key] ?? '')}</dd></div>)}</dl>
      <p>{text('Equipment required', 'Потрібна техніка')}: {strings(plan.required_equipment).join(', ') || text('Unknown', 'Невідомо')} · {string(plan.equipment_requirements_source) ?? text('Source unknown', 'Джерело невідоме')}</p>
      <p>{text('These checks do not validate agronomic suitability, hybrid adaptation or model accuracy.', 'Ці перевірки не підтверджують агрономічну придатність, адаптацію гібриду або точність моделі.')}</p>
    </details>
    <details><summary>{text('Financial and yield inputs', 'Фінансові дані та врожайність')}</summary>
      <p>{text('These values use supplied assumptions or published benchmarks, not validated yield predictions. Totals cover all chosen cycles; revenue is not profit.', 'Ці значення використовують надані припущення або опубліковані довідники, не перевірені прогнози врожайності. Підсумки охоплюють усі обрані цикли; виручка не є прибутком.')}</p>
      <dl><dt>{text('Marketable yield input total', 'Сумарна вхідна товарна врожайність')}</dt><dd>{format(economics.marketable_yield_t_ha, 't/ha')}</dd><dt>{text('Gross revenue', 'Виручка')}</dt><dd>{money(economics.gross_revenue_per_ha, economics.gross_revenue_currency)}</dd><dt>{text('Margin after included costs', 'Маржа після врахованих витрат')}</dt><dd>{money(economics.gross_margin_per_ha, economics.currency)}</dd></dl>
      <p>{text('Input origins', 'Походження входів')}: {strings(economics.input_origins).map(origin => ({ supplied: text('User supplied', 'Надані користувачем'), published_benchmark: text('Published benchmark', 'Опублікований довідник'), unavailable: text('Unavailable', 'Недоступні') })[origin] ?? origin).join(', ') || text('Unknown', 'Невідомо')}</p>
      <pre>{JSON.stringify(economics.inputs ?? [], null, 2)}</pre>
    </details>
    <details><summary>{text('Crop timeline and scenario water balance', 'Послідовність культур і водний баланс сценарію')}</summary>
      {members.length > 0 && <label>{text('Weather sequence', 'Погодна послідовність')}<select value={memberIndex} onChange={event => setMemberIndex(Number(event.target.value))}>{members.map((row, i) => <option key={i} value={i}>{string(row.historical_anchor_date) ?? text('Live weather only', 'Лише оперативна погода')}</option>)}</select></label>}
      {array(member.seasons).map((item, i) => { const cycle = object(item); const cropId = string(cycle.crop_id) ?? ''; const profile = catalog?.crops.find(row => row.id === cropId); return <section className="handoff-season" key={i}><h5>{uk ? profile?.name ?? cropId : cropNames[cropId] ?? cropId}</h5><p>{string(cycle.planting_date) ?? '—'} → {string(cycle.through_date) ?? '—'}</p><p>{text('Calendar', 'Календар')}: {string(cycle.calendar_id) ?? text('User stages', 'Етапи користувача')} · {array(cycle.stage_lengths).join(' / ')}</p>
        <dl><dt>{text('Opening root-zone reserve', 'Початковий запас кореневої зони')}</dt><dd>{format(cycle.opening_reserve_mm, 'mm')}</dd><dt>{text('Crop demand', 'Потреба культури')}</dt><dd>{format(cycle.demand_mm, 'mm')}</dd><dt>{text('Modeled water use', 'Змодельоване використання води')}</dt><dd>{format(cycle.modeled_use_mm, 'mm')}</dd><dt>{text('Ending reserve', 'Кінцевий запас')}</dt><dd>{format(cycle.ending_reserve_mm, 'mm')}</dd></dl>
        <p>{text('Opening source', 'Початкове джерело')}: {string(cycle.opening_source) ?? text('Unknown', 'Невідомо')}{string(cycle.opening_historical_date) ? ` · ${string(cycle.opening_historical_date)}` : ''}</p>
        <ul>{array(cycle.weather_segments).map((value, index) => { const segment = object(value), dates = object(segment.dates), historical = object(segment.historical_dates); return <li key={index}>{segment.source === 'live_forecast' ? text('Short forecast', 'Короткий прогноз') : text('Historical analogue', 'Історичний аналог')}: {string(dates.start)} → {string(dates.end)}{string(historical.start) ? ` (${historical.start} → ${historical.end})` : ''}</li> })}</ul>
      </section> })}
      {members.length === 0 && <p>{text('No scenario-level water balance was returned.', 'Водний баланс окремих сценаріїв не повернуто.')}</p>}
    </details>
  </article>
}
function SourceLink({ url, children }: { url: string; children: React.ReactNode }) {
  return /^https?:\/\//.test(url) ? <a href={url} target="_blank" rel="noreferrer">{children}</a> : <span>{children}: {url}</span>
}

function planName(value: unknown, uk: boolean) {
  const id = string(value)
  return id && /^plan-[abc]$/.test(id) ? `${uk ? 'План' : 'Plan'} ${id.slice(-1).toUpperCase()}` : id ?? (uk ? 'План без назви' : 'Unnamed plan')
}
