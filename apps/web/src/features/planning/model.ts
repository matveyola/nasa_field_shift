import type {
  ApplyPlanRequest, CandidateSequence, CropAssignment, FarmPlanningRequest, FieldProfileSnapshot,
  Metric, PlanAlternative, PlanningRun, SequenceEvaluation, WeatherScenarioSet,
} from '../../api/planningV1.generated'
import { decode } from './contracts.ts'
import { tr, type PlanningLocale } from './i18n.ts'

export interface PlanningEvidence { candidates: CandidateSequence[]; evaluations: SequenceEvaluation[] }
export interface PlanningContext { fieldId: string; season: number; trajectoryId: string }
export const clone = <T,>(value: T): T => structuredClone(value)
export const nullableNumber = (value: string): number | null => {
  if (!value.trim()) return null
  const number = Number(value)
  if (!Number.isFinite(number)) throw new Error('Enter a finite number')
  return number
}
export const cropKey = (crop: CropAssignment['crop']) => JSON.stringify([crop.crop_id, crop.cultivar_id, crop.profile_version])

/** Exact decimal strings preserve evidence rather than rounding a narrow interval away. */
export function metricText(metric: Metric | undefined, locale: PlanningLocale, currency?: string | null): string {
  const t = tr(locale)
  if (!metric) return t('Unknown')
  if (metric.status !== 'available') return t(metric.status)
  if (metric.unit.startsWith('currency') && !currency) return t('Unknown')
  const unit = metric.unit.replace('currency', currency ?? '')
  const range = metric.lower !== null && metric.upper !== null ? `${metric.lower}–${metric.upper}` : null
  const value = metric.value !== null ? String(metric.value) : range
  if (value === null) return t('Unknown')
  const intervalLabel = { none: '', scenario_range: t('Scenario range'), predictive_interval: t('Predictive interval'), measurement_interval: t('Measurement interval') }[metric.uncertainty.kind]
  return `${value}${range && metric.value !== null ? ` (${range})` : ''} ${unit}${intervalLabel ? ` · ${intervalLabel}` : ''}${metric.uncertainty.coverage !== null ? ` (${metric.uncertainty.coverage * 100}%)` : ''}`
}

export function distinctPlans(plans: PlanAlternative[]): PlanAlternative[] {
  const seen = new Set<string>()
  return plans.filter(plan => {
    const key = JSON.stringify(plan.assignments.map(a => [a.field_id, a.season, cropKey(a.crop)]).sort())
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function staleRun(run: PlanningRun, fields: FieldProfileSnapshot[], weather: WeatherScenarioSet, now: Date): boolean {
  if (new Date(run.request.weather.stale_after).getTime() <= now.getTime()) return true
  if (weather.scenario_set_id !== run.request.weather.scenario_set_id) return true
  return run.request.fields.some(saved => {
    const current = fields.find(field => field.field_id === saved.field_id)
    return !current || current.farm_id !== saved.farm_id || current.snapshot_id !== saved.snapshot_id
      || current.profile_revision !== saved.profile_revision || current.geometry_revision !== saved.geometry_revision
  })
}

export function runLabel(run: PlanningRun | null): string {
  if (!run) return 'No result yet'
  if (run.status !== 'succeeded') return { queued: 'Queued', running: 'Running', failed: 'Failed', cancelled: 'Cancelled' }[run.status]
  if (run.comparison?.solver_status === 'infeasible') return 'No feasible plan'
  if (run.comparison?.solver_status === 'limit_reached') return 'Search limit reached'
  if (run.comparison?.solver_status === 'error') return 'Failed'
  if (!run.comparison?.alternatives.length) return 'No alternatives'
  return 'Compare plans'
}

export function prepareRequest(draft: FarmPlanningRequest, selectedFields: string[], requestId: string, now: Date): FarmPlanningRequest {
  const request = clone(draft)
  request.request_id = requestId
  request.as_of = now.toISOString()
  request.fields = request.fields.filter(field => selectedFields.includes(field.field_id))
  const snapshotIds = new Set(request.fields.map(field => field.snapshot_id))
  request.weather.scenarios.forEach(s => { s.traces = s.traces.filter(t => snapshotIds.has(t.field_snapshot_id)) })
  if (!request.fields.length) throw new Error('Select at least one field')
  if (request.locks.some(lock => !selectedFields.includes(lock.field_id))) throw new Error('A committed field cannot be removed')
  if (new Date(request.weather.stale_after).getTime() <= now.getTime()) throw new Error('Stale report — refresh inputs and replan')
  if (new Date(request.weather.as_of).getTime() > now.getTime()) throw new Error('Weather was unavailable at planning time')
  if (request.fields.some(f => f.farm_id !== request.farm_id || new Date(f.as_of).getTime() > now.getTime())) throw new Error('Field snapshot scope or date mismatch')
  const trajectories = new Set(request.weather.scenarios.map(s => s.trajectory_id))
  if (request.weather.scenarios.some(s => s.traces.length !== snapshotIds.size)) throw new Error('Weather is missing for selected fields')
  if (request.goal === 'reference_margin' && !trajectories.has(request.reference_trajectory_id ?? '')) throw new Error('Choose a reference scenario')
  const seasons = new Set(Array.from({ length: request.season_count }, (_, i) => request.start_season + i))
  for (const lock of request.locks) {
    if (!seasons.has(lock.season) || !request.enabled_crops.some(c => cropKey(c) === cropKey(lock.crop))) throw new Error('A commitment conflicts with the selected seasons or crops')
  }
  const currencies = new Set([...request.resources, ...request.economics].map(r => r.currency).filter(Boolean))
  if (currencies.size > 1) throw new Error('Use one currency; no automatic currency conversion')
  for (const value of request.resources) {
    if (!seasons.has(value.season) || (value.budget !== null && !value.currency)) throw new Error('Budget requires a currency and a selected season')
  }
  for (const value of request.economics) {
    if (!seasons.has(value.season) || !trajectories.has(value.trajectory_id)) throw new Error('Economics outside the selected season or scenario')
    if ([value.price_per_t, value.variable_cost_per_ha, value.irrigation_cost_per_mm_ha].some(v => v !== null) && (!value.currency || !value.price_date)) throw new Error('Economic values require currency and price date')
    if (value.price_per_t !== null && !value.harvest_basis) throw new Error('Price requires harvest moisture basis')
  }
  return decode('request', request)
}

export function cellEvidence(run: PlanningRun, plan: PlanAlternative, assignment: CropAssignment, trajectoryId: string, evidence: PlanningEvidence) {
  const profile = run.request.fields.find(f => f.field_id === assignment.field_id)
  const candidates = evidence.candidates.filter(c => plan.sequence_ids.includes(c.sequence_id) && c.field_snapshot_id === profile?.snapshot_id
    && c.rulebook_version === run.request.rulebook_version)
  const assessments = candidates.flatMap(c => c.assessments).filter(a => a.season === assignment.season && cropKey(a.crop) === cropKey(assignment.crop))
  const evaluations = evidence.evaluations.filter(e => plan.evaluation_ids.includes(e.evaluation_id) && plan.sequence_ids.includes(e.sequence_id)
    && e.field_snapshot_id === profile?.snapshot_id && e.scenario_set_id === run.request.weather.scenario_set_id)
  const seasons = evaluations.flatMap(e => e.seasons.map(s => ({ model: e.model, applicability: e.applicability, scope: e.uncertainty_scope, ...s })))
    .filter(s => s.season === assignment.season && s.trajectory_id === trajectoryId && cropKey(s.crop) === cropKey(assignment.crop))
  return { assessments, seasons }
}

export function worstScenario(plan: PlanAlternative, request: FarmPlanningRequest): { value: number; currency: string } | null {
  const totals: { value: number; currency: string }[] = []
  for (const scenario of request.weather.scenarios) {
    const rows = plan.scenario_results.filter(r => r.trajectory_id === scenario.trajectory_id)
    if (rows.length !== request.season_count || new Set(rows.map(r => r.season)).size !== request.season_count) return null
    if (rows.some(r => r.season < request.start_season || r.season >= request.start_season + request.season_count)) return null
    if (rows.some(r => r.gross_margin.status !== 'available' || r.gross_margin.value === null || !r.currency)) return null
    if (new Set(rows.map(r => r.currency)).size !== 1) return null
    totals.push({ value: rows.reduce((total, r) => total + r.gross_margin.value!, 0), currency: rows[0].currency! })
  }
  if (!totals.length || new Set(totals.map(r => r.currency)).size !== 1) return null
  return totals.reduce((worst, row) => row.value < worst.value ? row : worst)
}

export function assignmentDiff(before: PlanAlternative | undefined, after: PlanAlternative): string[] {
  if (!before) return []
  const keys = new Set([...before.assignments, ...after.assignments].map(a => `${a.field_id}/${a.season}`))
  return [...keys].flatMap(key => {
    const a = before.assignments.find(a => `${a.field_id}/${a.season}` === key)
    const b = after.assignments.find(a => `${a.field_id}/${a.season}` === key)
    if (a && b && cropKey(a.crop) === cropKey(b.crop) && a.locked === b.locked) return []
    return [`${key}: ${a?.crop.crop_id ?? '—'} (${a?.crop.cultivar_id ?? '—'}) → ${b?.crop.crop_id ?? '—'} (${b?.crop.cultivar_id ?? '—'})`]
  })
}

export function applyRequest(run: PlanningRun, alternativeId: string, requestId: string): ApplyPlanRequest {
  if (run.status !== 'succeeded' || !run.comparison?.alternatives.some(a => a.alternative_id === alternativeId)) throw new Error('No applicable saved plan')
  if (!['optimal', 'feasible', 'limit_reached'].includes(run.comparison.solver_status)) throw new Error('No feasible plan')
  return { schema_version: '1.0.0', request_id: requestId, farm_id: run.request.farm_id, run_id: run.run_id,
    alternative_id: alternativeId, expected_input_sha256: run.input_sha256,
    expected_snapshots: clone(run.comparison.snapshots), confirmed_by_human: true }
}

/** Show the solver's actual farm objective, without converting it into ROI or a probability. */
export function objectiveSummary(plan: PlanAlternative, request: FarmPlanningRequest) {
  const label = { reference_margin: 'Reference scenario margin', worst_case_margin: 'Worst tested margin', water_demand: 'Water demand' }[request.goal]
  const currencies = new Set(plan.scenario_results.map(row => row.currency))
  const currency = currencies.size === 1 ? [...currencies][0] : null
  const unit = plan.objective_unit === 'currency' ? currency : plan.objective_unit === 'mm_ha' ? 'mm·ha' : 'days·ha'
  return { label, value: unit && Number.isFinite(plan.objective_value) ? plan.objective_value : null, unit }
}

export function excludedAssessments(run: PlanningRun, evidence: PlanningEvidence, context: PlanningContext) {
  const snapshot = run.request.fields.find(field => field.field_id === context.fieldId)?.snapshot_id
  const assessments = evidence.candidates.filter(candidate => candidate.field_id === context.fieldId && candidate.field_snapshot_id === snapshot && candidate.rulebook_version === run.request.rulebook_version)
    .flatMap(candidate => candidate.assessments).filter(assessment => assessment.season === context.season && assessment.status !== 'eligible')
  return [...new Map(assessments.map(assessment => [assessment.assessment_id, assessment])).values()]
}

/** User changes only the request horizon; immutable commitments must stay inside it. */
export function resizePlanningPeriod(draft: FarmPlanningRequest, seasonCount: FarmPlanningRequest['season_count']) {
  const end = draft.start_season + seasonCount
  const inside = (season: number) => season >= draft.start_season && season < end
  if (draft.locks.some(lock => !inside(lock.season))) throw new Error('A commitment conflicts with the selected seasons or crops')
  const next = clone(draft)
  next.season_count = seasonCount
  next.resources = next.resources.filter(row => inside(row.season))
  next.economics = next.economics.filter(row => inside(row.season))
  next.crop_areas = next.crop_areas.filter(row => inside(row.season))
  return next
}

export function usesUserYield(plan: PlanAlternative, request: FarmPlanningRequest) {
  const used = new Set(plan.scenario_results.flatMap(row => row.assumption_ids))
  return request.economics.some(row => used.has(row.assumption_id) && row.yield_origin === 'user_assumption')
}
