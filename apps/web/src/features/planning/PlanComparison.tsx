import { useRef } from 'react'
import type { PlanAlternative, PlanningRun } from '../../api/planningV1.generated'
import { assignmentDiff, cellEvidence, objectiveSummary, excludedAssessments, usesUserYield, distinctPlans, metricText, worstScenario, type PlanningContext, type PlanningEvidence } from './model'
import { cropName, tr, type PlanningLocale } from './i18n'
import { Reasons } from './EvidenceViews'

export function PlanComparison({ run, evidence, locale, context, onContext, alternativeId, onAlternative, previous, fieldNames = {}, compact = false }: {
  compact?: boolean; fieldNames?: Record<string, string>;
  run: PlanningRun; evidence: PlanningEvidence; locale: PlanningLocale; context: PlanningContext; onContext: (value: PlanningContext) => void;
  alternativeId: string; onAlternative: (id: string) => void; previous?: PlanAlternative
}) {
  const t = tr(locale)
  const fieldName = (id: string) => fieldNames[id] ?? id
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const plans = distinctPlans(run.comparison?.alternatives ?? [])
  const plan = plans.find(p => p.alternative_id === alternativeId) ?? plans[0]
  const years = Array.from({ length: run.request.season_count }, (_, i) => run.request.start_season + i)
  const assignment = plan?.assignments.find(a => a.field_id === context.fieldId && a.season === context.season)
  const details = assignment && plan ? cellEvidence(run, plan, assignment, context.trajectoryId, evidence) : null
  return <section className="pl-card"><h2>{t('Compare plans')}</h2>
    <Reasons reasons={run.comparison?.diagnostics ?? []} />
    {!plans.length && <section aria-label={t('Evidence')}>{evidence.candidates.filter(c => run.request.fields.some(f => f.snapshot_id === c.field_snapshot_id)).flatMap(c => c.assessments)
      .filter(a => a.season === context.season).map(a => <div className="pl-subcard" key={a.assessment_id}><strong>{t(a.status)}</strong>
        <p>{cropName(locale, a.crop.crop_id)} · {a.crop.cultivar_id ?? t('Unknown')} · {a.rulebook_version}</p><Reasons reasons={a.reasons} /></div>)}</section>}
    <div className="pl-cards">{plans.map(alternative => {
      const worst = worstScenario(alternative, run.request)
      const outcome = objectiveSummary(alternative, run.request)
      const cell = alternative.assignments.find(a => a.field_id === context.fieldId && a.season === context.season)
      const metrics = cell ? cellEvidence(run, alternative, cell, context.trajectoryId, evidence).seasons[0] : undefined
      return <article className="pl-subcard" key={alternative.alternative_id} data-selected={plan?.alternative_id === alternative.alternative_id}>
        <button type="button" className="button subtle" aria-pressed={plan?.alternative_id === alternative.alternative_id} onClick={() => onAlternative(alternative.alternative_id)}>{alternative.label}</button>
        <h3>{cell ? cropName(locale, cell.crop.crop_id) : t('Unknown')} · {context.season}</h3><p>{fieldName(context.fieldId)}</p>
        <div className="pl-outcome"><small>{t('Whole farm over the planning period')}</small><strong>{t(outcome.label)}: {outcome.value === null ? t('Unknown') : `${new Intl.NumberFormat(locale).format(outcome.value)} ${outcome.unit}`}</strong></div>
        <p>{t('Gross margin')} · {t('Not net profit')}</p>
        {usesUserYield(alternative, run.request) && <p>{t('Margin uses user-entered yield assumptions')}</p>}
        {alternative.scenario_results.length === 0 && <p>{t('Unknown')}</p>}
        <details open={!compact}><summary>{t('Margins by season and scenario')}</summary><ul>{alternative.scenario_results.map(row => <li key={`${row.trajectory_id}-${row.season}`}>{row.season} · {row.trajectory_id}: {metricText(row.gross_margin, locale, row.currency)}<br />
          {t('Irrigation')}: {row.irrigation_m3 ?? t('Unknown')} m³</li>)}</ul></details>
        <p>{t('Worst tested margin')}: {worst ? `${worst.value} ${worst.currency}` : t('Unknown')}</p>
        <p>{fieldName(context.fieldId)} · {context.season} · {context.trajectoryId}</p>
        <details><summary>{t('Sowing window')}</summary><p>{cell?.sowing_window ? `${cell.sowing_window.start} – ${cell.sowing_window.end}` : t('Not determined')}</p>
          <p>{t('Calendar constraint, not a weather-optimal date forecast')}</p>
          <p>{t('Weather-based sowing timing')}: {t('Not assessed')}</p>
          {!cell?.sowing_window && <p>{t('Review the regional calendar, soil temperature and moisture, weather and field access')}</p>}
          <small>{t('Saved assignment')} · {alternative.alternative_id} · {t('Rulebook')}: {run.request.rulebook_version}</small></details>
        <dl className="pl-metrics">
          <div><dt>{t('Yield')}</dt><dd>{metricText(metrics?.yield_t_ha, locale)}</dd>{metrics?.harvest_basis && <dd>{t(metrics.harvest_basis.basis)} · {metrics.harvest_basis.moisture_fraction}</dd>}</div>
          <div><dt>{t('Stress days')}</dt><dd>{metricText(metrics?.stress_days, locale)}</dd></div>
          <div><dt>{t('Irrigation')}</dt><dd>{metricText(metrics?.irrigation_mm, locale)}</dd></div>
        </dl>
        <div><strong>{t('Why this plan')}</strong><Reasons reasons={alternative.reasons} />{!alternative.reasons.length && <p>{t('No explanation supplied')}</p>}</div><p>{t('Tradeoffs depend on supplied scenarios and assumptions')}</p>
      </article>
    })}</div>
    {excludedAssessments(run, evidence, context).length > 0 && <details><summary>{t('Why other crops are unavailable')}</summary>
      {excludedAssessments(run, evidence, context).map(assessment => <article key={assessment.assessment_id}><h3>{cropName(locale, assessment.crop.crop_id)} · {t(assessment.status)}</h3><Reasons reasons={assessment.reasons} /></article>)}
    </details>}
    {plan && <><div className="pl-table-scroll"><table className="pl-matrix"><caption>{t('Field')} × {t('Season')}</caption><thead><tr><th>{t('Field')}</th>{years.map(y => <th key={y} scope="col">{y}</th>)}</tr></thead>
      <tbody>{run.request.fields.map((field, row) => <tr key={field.field_id}><th scope="row">{fieldName(field.field_id)}</th>{years.map((year, column) => {
        const cell = plan.assignments.find(a => a.field_id === field.field_id && a.season === year)
        const index = row * years.length + column
        const selected = context.fieldId === field.field_id && context.season === year
        return <td key={year}><button type="button" ref={el => { buttons.current[index] = el }} aria-pressed={selected}
          aria-label={`${fieldName(field.field_id)} ${year} ${cell ? cropName(locale, cell.crop.crop_id) : t('Unknown')}`}
          onClick={() => onContext({ ...context, fieldId: field.field_id, season: year })}
          onKeyDown={event => {
            const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: years.length, ArrowUp: -years.length }[event.key]
            if (delta === undefined) return
            event.preventDefault()
            const target = Math.max(0, Math.min(run.request.fields.length * years.length - 1, index + delta))
            buttons.current[target]?.focus()
          }}>{cell ? cropName(locale, cell.crop.crop_id) : t('Unknown')}{cell?.locked && <small> · {t('Locked')}</small>}</button></td>
      })}</tr>)}</tbody></table></div>
      {previous && <details open><summary>{t('Changes from previous plan')}</summary>{assignmentDiff(previous, plan).length ? <ul>{assignmentDiff(previous, plan).map(line => <li key={line}>{line}</li>)}</ul> : <p>{t('No assignment changes')}</p>}</details>}
      <section className="pl-subcard" aria-label={t('Evidence')}><details open={!compact}><summary>{t('Deterministic explanation from saved results')}</summary>
        <p className="pl-wrap">{run.run_id} · {run.comparison?.comparison_id} · {run.input_sha256}</p>
        {!assignment && <p>{t('Select a cell to inspect its evidence')}</p>}
        {assignment && <><p>{fieldName(assignment.field_id)} · {assignment.season} · {cropName(locale, assignment.crop.crop_id)} · {t('Cultivar')}: {assignment.crop.cultivar_id ?? t('Unknown')} · {assignment.crop.profile_version ?? t('Unknown')}</p>
          {assignment.locked && <p>{t('Locked')}: {assignment.commitment_id}</p>}
          {details?.assessments.map(a => <div key={a.assessment_id}><strong>{t(a.status)}</strong><p>{a.rulebook_version} · {a.assessment_id}</p><Reasons reasons={a.reasons} />
            {a.unchecked_rule_ids.length > 0 && <p>{t('insufficient_data')}: {a.unchecked_rule_ids.join(', ')}</p>}</div>)}
          {!details?.seasons.length && <p>{t('Missing model evidence')}</p>}
          {details?.seasons.map((row, i) => <div key={`${row.model.model_id}-${i}`}><p>{t(row.applicability)} · {row.model.model_id}@{row.model.version}</p><p>{row.scope}</p>
            <dl className="pl-metrics">
              <div><dt>{t('Yield')}</dt><dd>{metricText(row.yield_t_ha, locale)}</dd>{row.harvest_basis && <dd>{t(row.harvest_basis.basis)} · {row.harvest_basis.moisture_fraction}</dd>}</div>
              <div><dt>{t('Stress days')}</dt><dd>{metricText(row.stress_days, locale)}</dd></div>
              <div><dt>{t('Irrigation')}</dt><dd>{metricText(row.irrigation_mm, locale)}</dd></div>
              <div><dt>{t('Root-zone reserve')}</dt><dd>{metricText(row.root_zone_reserve_mm, locale)} · {row.root_depth_cm ?? t('Unknown')} cm</dd></div>
              <div><dt>{t('Surface moisture')}</dt><dd>{metricText(row.surface_moisture_m3_m3, locale)} · {row.surface_depth_top_cm ?? '?'}–{row.surface_depth_bottom_cm ?? '?'} cm</dd></div>
            </dl><Reasons reasons={row.reasons} />{[row.yield_t_ha, row.root_zone_reserve_mm, row.surface_moisture_m3_m3, row.stress_days, row.irrigation_mm].map((metric, i) => <Reasons key={i} reasons={metric.reasons} />)}</div>)}
        </>}
        <p>{t('No guaranteed profit')}</p>
      </details></section></>}
  </section>
}
