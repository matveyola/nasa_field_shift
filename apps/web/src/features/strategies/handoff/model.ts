import type { HandoffCatalog, HandoffJob, HandoffSubmission } from '../../../api/rotationHandoff'

export type CycleDraft = { planting: string; through: string; moisture: boolean; theta: [string, string, string]; moistureSource: string }
export type CropDraft = { cropId: string; calendarId: string; economics: boolean; yield: string; price: string; cost: string; currency: string; source: string }
export type PlanDraft = { id: string; crops: CropDraft[]; irrigation: Array<{ date: string; mm: string }> }
export type HandoffDraft = { cycles: CycleDraft[]; plans: PlanDraft[]; priority: string; liveDays: number; irrigationAccess: '' | 'yes' | 'no'; equipment: Record<string, string>; allowPublicData: boolean }
export const newCycle = (): CycleDraft => ({ planting: '', through: '', moisture: false, theta: ['', '', ''], moistureSource: '' })
export const newCrop = (): CropDraft => ({ cropId: '', calendarId: '', economics: false, yield: '', price: '', cost: '', currency: '', source: '' })
export function newDraft(irrigation?: boolean | null): HandoffDraft {
  return { cycles: [newCycle()], plans: ['plan-a', 'plan-b'].map(id => ({ id, crops: [newCrop()], irrigation: [] })), priority: '', liveDays: 7, irrigationAccess: irrigation === true ? 'yes' : irrigation === false ? 'no' : '', equipment: {}, allowPublicData: true }
}
export function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
export const strings = (value: unknown): string[] => array(value).filter((item): item is string => typeof item === 'string')
export const numeric = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null
export const string = (value: unknown): string | null => typeof value === 'string' ? value : null
export function equipmentOptions(catalog: HandoffCatalog) {
  return array(object(catalog).equipment).map(object).filter(row => typeof row.id === 'string' && typeof row.name === 'string').map(row => ({ id: row.id as string, name: row.name as string, purpose: string(row.purpose) ?? '' }))
}
export function priorityOptions(catalog: HandoffCatalog) {
  const schema = object(catalog.request_schemas.forecast)
  const farmer = object(object(schema.$defs).FarmerSettings)
  const priorities = object(object(farmer.properties).priorities)
  return strings(object(priorities.items).enum)
}
export function day(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const instant = Date.parse(`${value}T00:00:00Z`)
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value ? instant : null
}
function optionalNumber(value: string, name: string) {
  if (!value.trim()) return undefined
  const number = Number(value)
  if (!Number.isFinite(number) || number < 0) throw new Error(name)
  return number
}
/** Preserve missing values; no field, soil, economic or crop inputs are fabricated. */
export function forecastParameters(draft: HandoffDraft, catalog: HandoffCatalog, today: string): Record<string, unknown> {
  if (catalog.status !== 'available' || !catalog.operations.includes('forecast')) throw new Error('engine_unavailable')
  if (draft.cycles.length < 1 || draft.cycles.length > 3 || draft.plans.length < 2 || draft.plans.length > 3) throw new Error('plan_count')
  const start = day(today)
  if (start === null) throw new Error('planting_date')
  const max = start + 1098 * 86400000
  let previous = -Infinity
  const seasons = draft.cycles.map(cycle => {
    const planting = day(cycle.planting), through = cycle.through ? day(cycle.through) : null
    if (planting === null || planting < start || planting > max || planting <= previous || (cycle.through && (through === null || through < planting))) throw new Error('planting_date')
    previous = planting
    let initial: { theta: number[]; source: string } | undefined
    if (cycle.moisture) {
      const theta = cycle.theta.map(value => optionalNumber(value, 'moisture'))
      if (!cycle.moistureSource.trim() || theta.some(value => value === undefined || value > 1)) throw new Error('moisture')
      initial = { theta: theta as number[], source: cycle.moistureSource.trim() }
    }
    return { planting_date: cycle.planting, ...(cycle.through ? { through_date: cycle.through } : {}), ...(initial ? { initial_moisture: initial } : {}) }
  })
  const plans = draft.plans.map(plan => {
    if (plan.crops.length !== seasons.length) throw new Error('plan_count')
    let last = -Infinity
    const windows: Array<[number, number]> = []
    const crops = plan.crops.map((crop, index) => {
      const profile = catalog.crops.find(row => row.id === crop.cropId)
      const calendar = profile?.profile.calendars?.find(row => row.id === crop.calendarId)
      if (!calendar || calendar.stage_lengths.length !== 4 || calendar.stage_lengths.some(value => !Number.isInteger(value) || value <= 0)) throw new Error('calendar')
      const first = day(seasons[index].planting_date)!
      const naturalEnd = first + (calendar.stage_lengths.reduce((a, b) => a + b, 0) - 1) * 86400000
      const end = seasons[index].through_date ? Math.min(naturalEnd, day(seasons[index].through_date!)!) : naturalEnd
      if (first <= last || end > max) throw new Error('overlap')
      last = end; windows.push([first, end])
      let economics: Record<string, unknown> | undefined
      if (crop.economics) {
        const yieldValue = optionalNumber(crop.yield, 'economics'), price = optionalNumber(crop.price, 'economics'), cost = optionalNumber(crop.cost, 'economics')
        if (!crop.source.trim() || (price !== undefined || cost !== undefined) && !/^[A-Z]{3}$/.test(crop.currency) || (crop.currency && !/^[A-Z]{3}$/.test(crop.currency))) throw new Error('economics')
        if (yieldValue === undefined && price === undefined && cost === undefined) throw new Error('economics')
        economics = { source: crop.source.trim(), ...(yieldValue !== undefined ? { marketable_yield_t_ha: yieldValue } : {}), ...(price !== undefined ? { farmgate_price_per_t: price } : {}), ...(cost !== undefined ? { included_cost_per_ha: cost } : {}), ...(crop.currency ? { currency: crop.currency } : {}) }
      }
      return { crop_id: crop.cropId, calendar_id: crop.calendarId, ...(economics ? { economics } : {}) }
    })
    const seen = new Set<string>()
    const irrigation = plan.irrigation.map(event => {
      const date = day(event.date), mm = optionalNumber(event.mm, 'irrigation')
      if (date === null || mm === undefined || mm < 0.0001 || seen.has(event.date) || !windows.some(([first, end]) => date >= first && date <= end)) throw new Error('irrigation')
      seen.add(event.date)
      return { date: event.date, net_mm: mm }
    })
    return { id: plan.id, crops, ...(irrigation.length ? { irrigation } : {}) }
  })
  if (new Set(plans.map(plan => plan.id)).size !== plans.length) throw new Error('plan_count')
  if (draft.priority && !priorityOptions(catalog).includes(draft.priority)) throw new Error('priority')
  if (!Number.isInteger(draft.liveDays) || draft.liveDays < 1 || draft.liveDays > 10) throw new Error('live_days')
  const equipment = equipmentOptions(catalog)
  const access = Object.fromEntries(Object.entries(draft.equipment).filter(([, value]) => value !== 'unknown' && value !== ''))
  if (Object.entries(access).some(([id, value]) => !equipment.some(row => row.id === id) || !['owned', 'contracted', 'unavailable'].includes(value))) throw new Error('equipment')
  return { seasons, plans, live_days: draft.liveDays, farmer: { priorities: draft.priority ? [draft.priority] : [], irrigation_available: draft.irrigationAccess === '' ? null : draft.irrigationAccess === 'yes', ...(Object.keys(access).length ? { equipment_access: access } : {}) } }
}
export function matchesJob(job: HandoffJob, fieldId: string, revision: number, jobId?: string) {
  return job.field_id === fieldId && job.geometry_revision === revision && (!jobId || job.job_id === jobId)
}
export const activeJob = (job?: HandoffJob | null) => Boolean(job && ['queued', 'running', 'retrying'].includes(job.status))
export function newSubmission(parameters: Record<string, unknown>, revision: number, allow: boolean, id: string): HandoffSubmission {
  return { request_id: id, geometry_revision: revision, operation: 'forecast', parameters, allow_public_data: allow }
}
/** Only server-ranked, operationally feasible alternatives can lead the comparison. */
export function rankedRecommendations(payload: Record<string, unknown>, rankingUsable = false) {
  if (!rankingUsable || !strings(object(payload.decision).priorities_used).length) return []
  const ids = new Set(array(payload.plans).map(plan => string(object(plan).id)).filter(Boolean))
  return array(object(payload.decision).decisions).map(object).filter(row => ids.has(string(row.id)) && row.rank === 1 && row.feasibility === 'feasible')
}
