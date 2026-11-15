/** Synthetic presentation fixtures only. Never imported by production feature code. */
import pack from '../../../../contracts/planning-v1/cases.v1.json' with { type: 'json' }
import { example } from '../../../../contracts/planning-v1/examples/roundtrip.generated.ts'
import type { CropRef, Metric, PlanAlternative, PlanningContracts, PlanningRun, Reason, SequenceEvaluation, SuitabilityAssessment } from '../../src/api/planningV1.generated'
import type { PlanningEvidence } from '../../src/features/planning/model'

export const cases = pack.cases
export const now = () => new Date('2026-11-14T12:00:00Z')
const nullMetric = (unit: Metric['unit'], reason: Reason): Metric => ({ status: 'insufficient_data', value: null, lower: null, upper: null, unit,
  uncertainty: { kind: 'none', coverage: null, calibration_scope: null, method_version: 'synthetic-v1' }, reasons: [reason] })
const valueMetric = (unit: Metric['unit'], value: number, reason: Reason): Metric => ({ ...nullMetric(unit, reason), status: 'available', value, reasons: [] })
export const expectedStatus: Record<string, SuitabilityAssessment['status']> = {
  G01: 'ineligible', G02: 'insufficient_data', G03: 'eligible', G04: 'ineligible', G05: 'eligible', G06: 'eligible',
  G07: 'eligible', G08: 'insufficient_data', G09: 'unsupported', G10: 'ineligible', G11: 'unsupported', G12: 'eligible',
  G13: 'eligible', G14: 'out_of_domain', G15: 'eligible', G16: 'ineligible', G17: 'insufficient_data', G18: 'insufficient_data',
  G19: 'out_of_domain', G20: 'unsupported', G21: 'insufficient_data', G22: 'unsupported', G23: 'eligible', G24: 'eligible',
}

export function fixture(id = 'G23'): { bundle: PlanningContracts; run: PlanningRun; evidence: PlanningEvidence; crops: CropRef[] } {
  const specification = cases.find(c => c.id === id)
  if (!specification) throw new Error('Unknown frozen case')
  const bundle: PlanningContracts = structuredClone(example)
  const reason: Reason = { code: `synthetic-${id}`, message: `[${id}] ${specification.title} — synthetic UI response, not a model result.`,
    evidence: [{ id, kind: 'assumption', version: 'planning-acceptance-v1', locator: `cases.v1.json#${id}` }] }
  const crop = bundle.request.enabled_crops[0]
  if (['G09', 'G10', 'G11'].includes(id)) crop.crop_id = 'Rice'
  if (id === 'G20') crop.cultivar_id = 'unseen-hybrid'
  const second: CropRef = { crop_id: 'Soy', cultivar_id: null, profile_version: 'synthetic-soy' }
  const profile = bundle.request.fields[0]
  profile.field_id = id
  profile.missing_data = [reason]
  profile.current_crop = null
  const { longitude: lon, latitude: lat } = specification.location
  profile.geometry.coordinates = [[[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat + 0.001], [lon, lat]]]
  profile.terrain = { latitude_deg: lat, longitude_deg: lon, elevation_m: null, slope_deg: id === 'G15' ? 20 : null,
    aspect_deg: id === 'G15' ? 180 : null, aspect_convention: 'clockwise_from_true_north', solar_time: id === 'G15' ? '2027-06-21T12:00:00Z' : null,
    solar_zenith_deg: null, surface_shortwave_w_m2: null, source_ids: [] }
  bundle.request.season_count = 3
  bundle.request.enabled_crops = [crop, second]
  bundle.request.resources = []
  bundle.request.economics = []
  bundle.request.goal = 'water_demand'
  const assignments = [2027, 2028, 2029].map(season => ({ ...bundle.candidate.assignments[0], field_id: id, season, crop: structuredClone(crop) }))
  bundle.candidate = { ...bundle.candidate, field_id: id, assignments, assessments: assignments.map(a => ({ ...bundle.suitability,
    assessment_id: `assessment-${a.season}`, season: a.season, crop: a.crop, status: expectedStatus[id], reasons: [reason] })) }
  const metric = (unit: Metric['unit'], value: number) => valueMetric(unit, value, reason)
  const evaluation: SequenceEvaluation = { ...bundle.evaluation, seasons: assignments.map(a => ({ ...bundle.evaluation.seasons[0], season: a.season, crop: a.crop,
    yield_t_ha: id === 'G23' ? { ...metric('t/ha', 3), lower: 2, upper: 5, uncertainty: { kind: 'scenario_range', coverage: null, calibration_scope: null, method_version: 'synthetic-range' } } : nullMetric('t/ha', reason),
    harvest_basis: id === 'G23' ? { basis: 'standard_moisture', moisture_fraction: 0.14 } : null,
    stress_days: ['G05', 'G06'].includes(id) ? metric('days', id === 'G05' ? 30 : 10) : nullMetric('days', reason),
    irrigation_mm: ['G05', 'G06'].includes(id) ? metric('mm', id === 'G05' ? 0 : 150) : nullMetric('mm', reason),
    reasons: [reason] })) }
  bundle.evaluation = evaluation
  const alternative: PlanAlternative = { ...bundle.alternative, label: `A · ${id}`, assignments, reasons: [reason],
    scenario_results: assignments.map(a => ({ trajectory_id: 'reference', season: a.season, currency: null, gross_margin: nullMetric('currency', reason), irrigation_m3: null, variable_cost: null, assumption_ids: [] })) }
  const hasPlan = expectedStatus[id] === 'eligible'
  bundle.comparison = { ...bundle.comparison, solver_status: hasPlan ? 'feasible' : expectedStatus[id] === 'ineligible' || id === 'G21' ? 'infeasible' : 'not_run',
    alternatives: !hasPlan ? [] : [alternative, { ...structuredClone(alternative), alternative_id: 'alternative-2', label: `B · ${id}`,
      sequence_ids: ['sequence-other'], evaluation_ids: [], assignments: assignments.map(a => ({ ...a, crop: second })) }],
    snapshots: [{ ...bundle.comparison.snapshots[0], field_id: id }], diagnostics: [reason] }
  // Statuses are mocked presentations of the case specification, not scientific executions.
  const run: PlanningRun = { ...bundle.run, request: bundle.request, status: 'succeeded', comparison: bundle.comparison }
  bundle.field_profile = profile
  bundle.run = run
  return { bundle, run, evidence: { candidates: [bundle.candidate], evaluations: [evaluation] }, crops: [crop, second] }
}
