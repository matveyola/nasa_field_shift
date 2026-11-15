import type { HandoffResult, StrategyCandidate, StrategyPriority } from '../../api/rotationHandoff'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { cropNames } from './handoff/copy'
import { metric, metricDifference, priorityMetrics, strategyResult } from './strategyResults'

const accompanying = ['water_deficit', 'water_stress', 'yield', 'revenue', 'revenue_after_seed', 'soil_cover'] as const

export function useStrategyLabels() {
  const { language, crop, number, date } = useLocale()
  const uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const units: Record<string, string> = { mm: 'мм', '%': '%', days: 'дн.', 't/ha': 'т/га', pp: 'в. п.' }
  return {
    text, number, date,
    cropName: (id: string) => crop(cropNames[id] ?? id),
    priorityName: (id: StrategyPriority) => priorityMetrics[id][uk ? 4 : 3],
    formatMetric: (value: { amount: number; unit: string } | null, signed = false) => value
      ? `${number(value.amount, 2, signed)} ${uk ? (units[value.unit] ?? value.unit.replace('/ha', '/га')) : value.unit}`
      : text('Unknown', 'Невідомо'),
  }
}

function CandidateDetails({ candidate }: { candidate: StrategyCandidate }) {
  const { text, number, date } = useStrategyLabels()
  const agronomy = candidate.agronomy?.candidate as {
    previous_same_crop_season_year?: number | null
    disease_guidance?: { minimum_return_interval_years?: number | null; source?: string | null } | null
  } | undefined
  const scenarios = Object.entries(candidate.scenarios ?? {}) as [string, {
    fallow_days_before_planting?: number | null
    recharged_to_80pct_after_previous_end_on?: string | null
  }][]
  const seed = candidate.economics?.seed_cost as { source?: string } | null | undefined
  const source = candidate.economics?.source
  const years = candidate.economics?.price_reference_years
  return <details>
    <summary>{text('Water recovery, return interval and sources', 'Відновлення запасу води, перерва й джерела')}</summary>
    {agronomy?.previous_same_crop_season_year != null && <p>{text('Last known sowing year for this crop', 'Останній відомий рік сівби цієї культури')}: {agronomy.previous_same_crop_season_year}</p>}
    <p>{text('Minimum crop return interval for this region', 'Мінімальна перерва повернення культури для цього регіону')}: {agronomy?.disease_guidance?.minimum_return_interval_years == null
      ? text('Unknown', 'Невідомо') : `${agronomy.disease_guidance.minimum_return_interval_years} ${text('years', 'р.')}`}</p>
    {agronomy?.disease_guidance?.source && <p>{agronomy.disease_guidance.source}</p>}
    {(candidate.warnings ?? []).map(warning => <p key={warning}>{warning === 'below_regional_crop_return_interval'
      ? text('This sowing date is earlier than the regional return interval.', 'Дата сівби раніша за регіонально рекомендовану перерву.') : warning}</p>)}
    {scenarios.map(([id, scenario]) => <p key={id}>{id} · {text('Gap without a living crop', 'Проміжок без живої культури')}: {scenario.fallow_days_before_planting == null
      ? text('Unknown', 'Невідомо') : `${number(scenario.fallow_days_before_planting, 0)} ${text('days', 'дн.')}`} · {text('80% water recovery', 'Відновлення запасу води до 80%')}: {scenario.recharged_to_80pct_after_previous_end_on
      ? date(scenario.recharged_to_80pct_after_previous_end_on) : text('No date reported', 'Дату не визначено')}</p>)}
    {typeof source === 'string' && source && <p>{text('Economic reference', 'Економічний довідник')}: {source}{Array.isArray(years) && years.length > 0 ? ` · ${years.join(', ')}` : ''}</p>}
    {seed?.source && <p>{text('Seed cost source', 'Джерело вартості насіння')}: {seed.source}</p>}
    {typeof candidate.erosion?.t_ha_year === 'number' && <p>{text('Estimated annual soil loss (RUSLE)', 'Розраховані річні втрати ґрунту (RUSLE)')}: {number(candidate.erosion.t_ha_year)} {text('t/ha/year', 'т/га/рік')}</p>}
  </details>
}

export function StrategyCandidateCard({ candidate, priority, ranked, choose }: {
  candidate: StrategyCandidate; priority: StrategyPriority; ranked?: boolean; choose?: () => void
}) {
  const { text, cropName, priorityName, formatMetric } = useStrategyLabels()
  const selected = [...new Set([priority, ...accompanying])]
  const isAvailable = candidate.status === 'ok'
  const label = cropName(candidate.crop_id)
  return <article className="strategy-decision strategy-candidate">
    <h4>{ranked && candidate.rank != null && <span>{candidate.rank}. </span>}<CropIcon crop={candidate.crop_id} />{label}</h4>
    {!isAvailable && <p role="status">{text('Assessment unavailable', 'Оцінка недоступна')}{candidate.reason && ` · ${candidate.reason}`}</p>}
    <dl className="strategy-metrics">{selected.map(id => <div key={id} data-primary={id === priority}>
      <dt>{priorityName(id)}</dt><dd>{formatMetric(isAvailable ? metric(candidate, id) : null)}</dd>
    </div>)}</dl>
    {isAvailable && <CandidateDetails candidate={candidate} />}
    {choose && <button type="button" className="button" onClick={choose} aria-label={`${text('Choose', 'Обрати')} ${label}`}>{text('Choose this crop', 'Обрати цю культуру')}</button>}
  </article>
}

export function StrategyResultCards({ result, priority, comparison, choose }: {
  result: HandoffResult; priority: StrategyPriority; comparison?: [string, string]; choose?: (candidate: StrategyCandidate) => void
}) {
  const { text, cropName, formatMetric } = useStrategyLabels()
  const { candidates, picks, ranked } = strategyResult(result)
  const pair = comparison?.map(id => candidates.find(row => row.crop_id === id) ?? { crop_id: id, status: 'unavailable' as const, rank: null })
  const cards = pair ?? picks
  const delta = pair?.length === 2 && pair.every(row => row.status === 'ok') ? metricDifference(pair[0], pair[1], priority) : null
  const excluded = !pair && candidates.filter(row => !picks.some(pick => pick.crop_id === row.crop_id))
  return <section className="strategy-result" aria-label={text('Assessment results', 'Результати оцінки')}>
    <h3>{pair ? text('Two crops under the same conditions', 'Дві культури за однакових умов') : text(`Available crops: ${picks.length} of 10`, `Доступні культури: ${picks.length} із 10`)}</h3>
    {!pair && !ranked && cards.length > 0 && <p role="status">{text('A ranking is unavailable for these data. The choices are shown without ranks.', 'За цими даними рейтинг недоступний. Варіанти показано без місць.')}</p>}
    {!pair && picks.length < 10 && <p>{text('The count reflects model support and constraints for this field and date.', 'Кількість залежить від підтримки моделі та обмежень для поля й дати.')}</p>}
    {pair && <p role="status">{text('Difference (second crop minus first)', 'Різниця (друга культура мінус перша)')}: {formatMetric(delta, true)}</p>}
    {result.reasons.map(reason => <p key={reason} role="status">{reason}</p>)}
    {result.diagnostics.map((item, i) => <p key={`${item.code}-${i}`} role="status">{item.message}</p>)}
    <div className={pair ? 'strategy-decisions strategy-comparison' : 'strategy-decisions'}>
      {cards.map(candidate => <StrategyCandidateCard key={candidate.crop_id} candidate={candidate} priority={priority} ranked={!pair && ranked}
        choose={choose ? () => choose(candidate) : undefined} />)}
    </div>
    {excluded && excluded.length > 0 && <details><summary>{text('Other assessed crops and limits', 'Інші оцінені культури та обмеження')}</summary>
      <ul>{excluded.map(row => <li key={row.crop_id}>{cropName(row.crop_id)}{row.reason && ` · ${row.reason}`}
        {(row.blockers ?? []).map(blocker => <p key={blocker}>{blocker}</p>)}<CandidateDetails candidate={row} /></li>)}</ul>
    </details>}
    {(result.limitations.length > 0 || typeof result.payload.weather_dataset === 'string') && <details>
      <summary>{text('Scenario source and limitations', 'Джерело й обмеження сценарію')}</summary>
      {typeof result.payload.weather_dataset === 'string' && <p>{result.payload.weather_dataset}</p>}
      <ul>{result.limitations.map((note, index) => <li key={index}>{note}</li>)}</ul>
    </details>}
  </section>
}
