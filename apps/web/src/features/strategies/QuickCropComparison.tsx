import { TermInfo } from '../../shared/TermInfo'
import { useState, type FormEvent } from 'react'
import type { Field } from '../../api/contracts'
import type { HandoffCrop, HandoffHybrid, StrategyContext, StrategyPriority } from '../../api/rotationHandoff'
import { ErrorBox, Loading } from '../../shared/ui'
import { useAccess } from '../access/AccessProvider'
import { SowingDateField } from './SowingDateField'
import { StrategyCandidateCard, useStrategyLabels } from './StrategyResultCards'
import { priorityMetrics, strategyChoiceId, strategyResult, validPlantingDate } from './strategyResults'
import { useStrategyJob } from './useStrategyJob'

export function QuickCropComparison({ field, context, crops, hybrids, ready }: {
  field: Field; context: StrategyContext; crops: HandoffCrop[]; hybrids: HandoffHybrid[]; ready: boolean
}) {
  const { text, candidateName, priorityName, date } = useStrategyLabels()
  const { canEdit } = useAccess()
  const [first, setFirst] = useState('')
  const [second, setSecond] = useState('')
  const [planting, setPlanting] = useState('')
  const [priority, setPriority] = useState<StrategyPriority>('water_deficit')
  const [dailyVolume, setDailyVolume] = useState('')
  const job = useStrategyJob(field)
  const options = crops.flatMap(crop => [
    { id: crop.id, crop_id: crop.id },
    ...hybrids.filter(hybrid => hybrid.crop_id === crop.id).map(hybrid =>
      ({ id: hybrid.choice_id, crop_id: crop.id, choice: { hybrid } })),
  ])
  const volume = Number(dailyVolume)
  const irrigationValid = priority !== 'water_saving' || (context.area_ha > 0 && volume > 0 && volume <= 1_000_000)
  const valid = ready && canEdit && !job.busy && first !== second &&
    options.some(option => option.id === first) && options.some(option => option.id === second) &&
    validPlantingDate(planting, context) && irrigationValid

  function compare(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    void job.send({ request_id: crypto.randomUUID(), geometry_revision: field.boundary_revision,
      mode: 'compare', planting_date: planting, step_count: 1, priority, previous: [],
      comparison_crops: [first, second],
      irrigation: priority === 'water_saving' ? { status: 'available', daily_m3: volume } : { status: 'unknown' },
    })
  }

  const result = job.job?.result
  const assessment = result && strategyResult(result)
  const winner = assessment?.ranked && assessment.candidates.filter(row => row.rank === 1)
  return <section className="strategy-flow" aria-label={text('Quick crop comparison', 'Швидке порівняння культур')}>
    <p>{text('Choose two crops or hybrids, a shared sowing date and a criterion. Both options use the same field and weather scenarios.',
      'Оберіть два варіанти (культури або гібриди), спільну дату сівби й критерій. Обидва варіанти оцінюються для того самого поля за однакової погоди.')}</p>
    <form className="strategy-form" onSubmit={compare}>
      <div className="strategies-context">
        {[{ value: first, other: second, label: text('First crop', 'Перша культура'), set: setFirst },
          { value: second, other: first, label: text('Second crop', 'Друга культура'), set: setSecond }].map(({ value, other, label, set }) =>
          <label key={label}>{label}<select value={value} required onChange={event => { job.clear(); set(event.target.value) }}>
            <option value="">{text('Choose a crop', 'Оберіть культуру')}</option>
            {options.map(row => <option key={row.id} value={row.id} disabled={row.id === other}>{candidateName(row)}</option>)}
          </select></label>)}
        <div className="strategy-input-control"><div className="context-help-row">
          <label htmlFor="comparison-priority">{text('Comparison criterion', 'Критерій порівняння')}</label>
          <TermInfo term={priority} label={priorityName(priority)} />
        </div><select id="comparison-priority" value={priority} onChange={event => { job.clear(); setPriority(event.target.value as StrategyPriority) }}>
            {(Object.keys(priorityMetrics) as StrategyPriority[]).map(item => <option key={item} value={item}>{priorityName(item)}</option>)}
          </select>
        </div>
        {priority === 'water_saving' && <label>{text('Available irrigation water, m³/day', 'Доступна вода для поливу, м³/добу')}
          <input type="number" inputMode="decimal" min="0" max="1000000" step="any" required value={dailyVolume}
            onChange={event => { job.clear(); setDailyVolume(event.target.value) }} />
        </label>}
        <SowingDateField id="compare-planting-date" context={context} value={planting}
          onChange={value => { job.clear(); setPlanting(value) }} />
      </div>
      <button type="submit" className="button primary" disabled={!valid}>{text('Compare crops', 'Порівняти культури')}</button>
    </form>
    {job.busy && !job.error && <Loading text={text('Comparing crops…', 'Порівнюємо культури…')} />}
    {job.error && <ErrorBox message={job.error} retry={job.retry} />}
    {job.job?.status === 'failed' && <ErrorBox message={job.job.error ?? text('Comparison failed.', 'Порівняння не вдалося.')} />}
    {result && assessment && <section className="strategy-result" aria-label={text('Comparison results', 'Результати порівняння')}>
      <h3>{text('Comparison results', 'Результати порівняння')}</h3>
      <p>{priorityName(priority)} · {date(planting)}</p>
      {winner && winner.length === 1 && <p>{text('Better on this criterion', 'Кращий результат за цим критерієм')}: {candidateName(winner[0])}</p>}
      {winner && winner.length > 1 && <p>{text('Equal results on this criterion.', 'Однакові результати за цим критерієм.')}</p>}
      {!assessment.ranked && <p>{text('A ranking is unavailable for this criterion under these conditions.', 'За цих умов рейтинг за обраним критерієм недоступний.')}</p>}
      {result.reasons.map(reason => <p key={reason} role="status">{reason}</p>)}
      {result.diagnostics.map((item, index) => <p key={`${item.code}-${index}`}>{item.message}</p>)}
      <div className="strategy-decisions">
        {[first, second].map(id => {
          const row = assessment.candidates.find(candidate => strategyChoiceId(candidate) === id)
          const option = options.find(item => item.id === id)
          return <StrategyCandidateCard key={id} priority={priority}
            candidate={row ?? { ...option, crop_id: option?.crop_id ?? id, status: 'unavailable', rank: null }} />
        })}
      </div>
    </section>}
  </section>
}
