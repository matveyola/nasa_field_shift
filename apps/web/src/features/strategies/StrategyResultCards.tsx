import { TermInfo } from '../../shared/TermInfo'
import type { HandoffResult, StrategyCandidate, StrategyPriority } from '../../api/rotationHandoff'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { cropNames } from './handoff/copy'
import { metric, priorityMetrics, strategyChoiceId, strategyResult } from './strategyResults'

const accompanying = ['water_deficit', 'water_stress', 'yield', 'revenue', 'revenue_after_seed', 'soil_cover'] as const

export function useStrategyLabels() {
  const { language, crop, number, date } = useLocale()
  const uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const cropName = (id: string) => crop(cropNames[id] ?? id)
  const units: Record<string, string> = { mm: 'мм', '%': '%', days: 'дн.', 't/ha': 'т/га', pp: 'в. п.' }
  return {
    text, number, date,
    cropName,
    candidateName: (row: Pick<StrategyCandidate, 'crop_id' | 'choice'>) => row.choice?.hybrid
      ? `${cropName(row.crop_id)} · ${row.choice.hybrid.name}` : cropName(row.crop_id),
    priorityName: (id: StrategyPriority) => priorityMetrics[id][uk ? 4 : 3],
    formatMetric: (value: { amount: number; unit: string } | null) => value
      ? `${number(value.amount, 2)} ${uk ? (units[value.unit] ?? value.unit.replace('/ha', '/га')) : value.unit}`
      : text('Unknown', 'Невідомо'),
  }
}

function CandidateDetails({ candidate }: { candidate: StrategyCandidate }) {
  const { text, number, date, formatMetric } = useStrategyLabels()
  const agronomy = candidate.agronomy?.candidate as {
    previous_same_crop_season_year?: number | null
    disease_guidance?: { minimum_return_interval_years?: number | null; source?: string | null } | null
  } | undefined
  const scenarios = Object.entries(candidate.scenarios ?? {})
  const hybrid = candidate.choice?.hybrid
  const seed = candidate.economics?.seed_cost as {
    source?: string; geography?: string; year?: number; cost_per_ha?: number; currency?: string
    original_cost_per_ha?: number; original_currency?: string
    exchange_rate?: { date: string; source: string }
  } | null | undefined
  const source = candidate.economics?.source
  const years = candidate.economics?.price_reference_years
  return <details>
    <summary>{text('Water recovery, return interval and sources', 'Відновлення запасу води, перерва й джерела')}</summary>
    {agronomy?.previous_same_crop_season_year != null && <p>{text('Last known sowing year for this crop', 'Останній відомий рік сівби цієї культури')}: {agronomy.previous_same_crop_season_year}</p>}
    <div className="context-help-row"><span>{text('Minimum crop return interval for this region', 'Мінімальна перерва повернення культури для цього регіону')} · {agronomy?.disease_guidance?.minimum_return_interval_years == null
      ? text('Unknown', 'Невідомо') : `${agronomy.disease_guidance.minimum_return_interval_years} ${text('years', 'р.')}`}</span>
      <TermInfo term="return_interval" label={text('Crop return interval', 'Перерва повернення культури')} />
    </div>
    {agronomy?.disease_guidance?.source && <p>{agronomy.disease_guidance.source}</p>}
    {(candidate.warnings ?? []).map(warning => <p key={warning}>{warning === 'below_regional_crop_return_interval'
      ? text('This sowing date is earlier than the regional return interval.', 'Дата сівби раніша за регіонально рекомендовану перерву.') : warning}</p>)}
    {scenarios.map(([id, scenario]) => <div key={id}>
      <p>{id}</p>
      {scenario.modeled_flowering_on && <p>{text('Estimated flowering', 'Оцінена дата цвітіння')} · {date(scenario.modeled_flowering_on)}</p>}
      {hybrid && scenario.end_date && <p>{text('Modelled end of growth', 'Змодельоване завершення росту')} · {date(scenario.end_date)}</p>}
      <div className="context-help-row">
        <span>{text('Gap without a living crop', 'Проміжок без живої культури')} · {scenario.fallow_days_before_planting == null
          ? text('Unknown', 'Невідомо') : `${number(scenario.fallow_days_before_planting, 0)} ${text('days', 'дн.')}`}</span>
        <TermInfo term="fallow" label={text('Gap without a living crop', 'Проміжок без живої культури')} />
      </div>
      <div className="context-help-row">
        <span>{text('80% water recovery', 'Відновлення запасу води до 80%')} · {scenario.recharged_to_80pct_after_previous_end_on
          ? date(scenario.recharged_to_80pct_after_previous_end_on) : text('No date reported', 'Дату не визначено')}</span>
        <TermInfo term="recharge" label={text('80% water recovery', 'Відновлення запасу води до 80%')} />
      </div>
    </div>)}
    {hybrid && <a href={hybrid.source_url} target="_blank" rel="noreferrer">
      {text('Source for hybrid development', 'Джерело для фаз розвитку гібрида')}
    </a>}
    {typeof source === 'string' && source && <p>{text('Economic reference', 'Економічний довідник')}: {source}{Array.isArray(years) && years.length > 0 ? ` · ${years.join(', ')}` : ''}</p>}
    {typeof seed?.cost_per_ha === 'number' && seed.currency && <p>
      {text('Seed cost used', 'Вартість насіння в розрахунку')} · {formatMetric({ amount: seed.cost_per_ha, unit: `${seed.currency}/ha` })}
      {seed.geography && ` · ${seed.geography}`}{seed.year && ` · ${seed.year}`}
    </p>}
    {seed?.exchange_rate && typeof seed.original_cost_per_ha === 'number' && seed.original_currency && <p>
      {text('Original seed cost', 'Вартість насіння в оригінальній валюті')} · {formatMetric({ amount: seed.original_cost_per_ha, unit: `${seed.original_currency}/ha` })}
      {' · '}{text('Exchange rate dated', 'Курс за')}{' '}{date(seed.exchange_rate.date)}{' · '}{seed.exchange_rate.source}
    </p>}
    {seed?.source && <p>{text('Seed cost source', 'Джерело вартості насіння')} · {seed.source}</p>}
    {typeof candidate.erosion?.t_ha_year === 'number' && <div className="context-help-row">
      <span>{text('Estimated annual soil loss (RUSLE)', 'Розраховані річні втрати ґрунту (RUSLE)')} · {number(candidate.erosion.t_ha_year)} {text('t/ha/year', 'т/га/рік')}</span>
      <TermInfo term="erosion" label={text('Water erosion', 'Водна ерозія')} />
    </div>}
  </details>
}

export function StrategyCandidateCard({ candidate, priority, ranked, choose }: {
  candidate: StrategyCandidate; priority: StrategyPriority; ranked?: boolean; choose?: () => void
}) {
  const { text, candidateName, priorityName, formatMetric } = useStrategyLabels()
  const selected = [...new Set([priority, ...accompanying])]
  const isAvailable = candidate.status === 'ok'
  const label = candidateName(candidate)
  return <article className="strategy-decision strategy-candidate">
    <h4>{ranked && candidate.rank != null && <span>{candidate.rank}. </span>}<CropIcon crop={candidate.crop_id} />{label}</h4>
    {!isAvailable && <p role="status">{text('Assessment unavailable', 'Оцінка недоступна')}{candidate.reason && ` · ${candidate.reason}`}</p>}
    <dl className="strategy-metrics">{selected.map(id => <div key={id} data-primary={id === priority}>
      <dt className="context-help-row">{priorityName(id)}<TermInfo term={id} label={priorityName(id)} /></dt><dd>{formatMetric(isAvailable ? metric(candidate, id) : null)}</dd>
    </div>)}</dl>
    {isAvailable && candidate.choice?.hybrid && <p>{text(
      'Water metrics use this hybrid’s development dates. Reference yield and prices are for the crop as a whole.',
      'Водні показники враховують фази розвитку цього гібрида. Довідкова врожайність і ціни наведені для культури загалом.',
    )}</p>}
    {isAvailable && <CandidateDetails candidate={candidate} />}
    {choose && <button type="button" className="button" onClick={choose} aria-label={`${text('Choose', 'Обрати')} ${label}`}>{text('Choose this crop', 'Обрати цю культуру')}</button>}
  </article>
}

export function StrategyResultCards({ result, priority, choose }: {
  result: HandoffResult; priority: StrategyPriority; choose: (candidate: StrategyCandidate) => void
}) {
  const { text, candidateName } = useStrategyLabels()
  const { candidates, picks, ranked } = strategyResult(result)
  const pickIds = new Set(picks.map(strategyChoiceId))
  const excluded = candidates.filter(row => !pickIds.has(strategyChoiceId(row)))
  return <section className="strategy-result" aria-label={text('Assessment results', 'Результати оцінки')}>
    <h3>{text(`Available options: ${picks.length} of ${candidates.length}`, `Доступні варіанти: ${picks.length} із ${candidates.length}`)}</h3>
    {!ranked && picks.length > 0 && <p role="status">{text('The model returned crop estimates without a ranking.', 'Модель розрахувала показники культур без рейтингу.')}</p>}
    {result.reasons.map(reason => <p key={reason} role="status">{reason}</p>)}
    {result.diagnostics.map((item, i) => <p key={`${item.code}-${i}`} role="status">{item.message}</p>)}
    <div className="strategy-decisions">
      {picks.map(candidate => <StrategyCandidateCard key={strategyChoiceId(candidate)} candidate={candidate} priority={priority}
        ranked={ranked} choose={() => choose(candidate)} />)}
    </div>
    {excluded.length > 0 && <details><summary>{text('Other assessed crops and limits', 'Інші оцінені культури та обмеження')}</summary>
      <ul>{excluded.map(row => <li key={strategyChoiceId(row)}>{candidateName(row)}{row.reason && ` · ${row.reason}`}
        {(row.blockers ?? []).map(blocker => <p key={blocker}>{blocker}</p>)}<CandidateDetails candidate={row} /></li>)}</ul>
    </details>}
    {(result.limitations.length > 0 || typeof result.payload.weather_dataset === 'string') && <details>
      <summary>{text('Scenario source and limitations', 'Джерело й обмеження сценарію')}</summary>
      {typeof result.payload.weather_dataset === 'string' && <p>{result.payload.weather_dataset}</p>}
      <ul>{result.limitations.map((note, index) => <li key={index}>{note}</li>)}</ul>
    </details>}
  </section>
}
