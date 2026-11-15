import type {
  ApplyPlanRequest, ApplyPlanResult, FarmPlanningRequest, FieldProfileSnapshot, PlanningRun,
  ProfileWriteRequest, ReplanRequest, PlanningError, Reason,
} from './planningV1.generated'
import { decode } from '../features/planning/contracts.ts'

export type PlanningTransport = <T>(path: string, options?: RequestInit) => Promise<T>
export class PlanningApiFailure extends Error {
  status: number
  code: PlanningError['code']
  reasons: Reason[]
  constructor(status: number, error: PlanningError) {
    super(({ stale: 'Stale report — refresh inputs and replan', conflict: 'The saved request conflicts with current data',
      forbidden: 'You do not have permission for this action', not_found: 'The saved plan or field was not found',
      invalid_input: 'Review required fields and units', unavailable: 'Backend unavailable — no replacement data',
      internal: 'Backend unavailable — no replacement data' })[error.code])
    this.status = status; this.code = error.code; this.reasons = error.reasons
  }
}

/** Shared session headers/base; preserve planning-v1 error bodies that legacy request() discards. */
export const planningTransport: PlanningTransport = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const { apiBase, applicationHeaders } = await import('./client')
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(options?.method?.toUpperCase() ?? 'GET')
  let response: Response
  try {
    response = await fetch(apiBase + path, { ...options, credentials: 'same-origin', headers: {
      Accept: 'application/json', ...(unsafe ? applicationHeaders() : {}),
      ...(options?.body ? { 'Content-Type': 'application/json' } : {}), ...options?.headers,
    } })
  } catch (error) {
    if (options?.signal?.aborted) throw error
    throw new Error('Backend unavailable — no replacement data')
  }
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('field-shift-session-expired'))
    let error: PlanningError
    try { error = decode('error', body) }
    catch {
      const code = response.status === 403 || response.status === 401 ? 'forbidden' : response.status === 404 ? 'not_found'
        : response.status === 409 ? 'conflict' : response.status === 422 ? 'invalid_input' : 'unavailable'
      error = { schema_version: '1.0.0', request_id: null, code, retryable: response.status >= 500, reasons: [] }
    }
    throw new PlanningApiFailure(response.status, error)
  }
  return body as T
}
export interface PlanningApi {
  profile(farm: string, field: string, signal?: AbortSignal): Promise<FieldProfileSnapshot>
  saveProfile(body: ProfileWriteRequest, signal?: AbortSignal): Promise<FieldProfileSnapshot>
  submit(body: FarmPlanningRequest, signal?: AbortSignal): Promise<PlanningRun>
  run(farm: string, run: string, signal?: AbortSignal): Promise<PlanningRun>
  list(farm: string, signal?: AbortSignal): Promise<PlanningRun[]>
  apply(body: ApplyPlanRequest, signal?: AbortSignal): Promise<ApplyPlanResult>
  replan(body: ReplanRequest, signal?: AbortSignal): Promise<PlanningRun>
}
const segment = (id: string) => encodeURIComponent(id)
const farmPath = (farm: string) => `/planning-v1/farms/${segment(farm)}`
const json = (body: { request_id?: string }, method: string, signal?: AbortSignal): RequestInit => ({
  method, signal, body: JSON.stringify(body),
  headers: body.request_id ? { 'Idempotency-Key': body.request_id } : {},
})

/** Reuse session/CSRF transport; importing the adapter alone never starts a request. */
export function createPlanningApi(transport: PlanningTransport = planningTransport): PlanningApi {
  const scopedRun = (value: unknown, farm: string, id?: string) => {
    const run = decode('run', value)
    if (run.request.farm_id !== farm || (id && run.run_id !== id)) throw new Error('Planning response scope mismatch')
    return run
  }
  return {
    async profile(farm, field, signal) {
      const result = decode('field_profile', await transport(`${farmPath(farm)}/fields/${segment(field)}/profile`, { signal }))
      if (result.farm_id !== farm || result.field_id !== field) throw new Error('Profile response scope mismatch')
      return result
    },
    async saveProfile(body, signal) {
      decode('profile_write', body)
      const { farm_id, field_id } = body.profile
      const result = decode('field_profile', await transport(`${farmPath(farm_id)}/fields/${segment(field_id)}/profile`, json(body, 'PUT', signal)))
      if (result.farm_id !== farm_id || result.field_id !== field_id) throw new Error('Profile response scope mismatch')
      if (body.expected_revision !== null && result.profile_revision <= body.expected_revision) throw new Error('Profile response has not advanced its revision')
      return result
    },
    async submit(body, signal) {
      decode('request', body)
      const run = scopedRun(await transport(`${farmPath(body.farm_id)}/runs`, json(body, 'POST', signal)), body.farm_id)
      if (run.request.request_id !== body.request_id) throw new Error('Planning request identity mismatch')
      return run
    },
    async run(farm, id, signal) {
      return scopedRun(await transport(`${farmPath(farm)}/runs/${segment(id)}`, { signal }), farm, id)
    },
    async list(farm, signal) {
      const results = await transport<unknown>(`${farmPath(farm)}/runs`, { signal })
      if (!Array.isArray(results)) throw new Error('Invalid planning run list')
      return results.map(result => scopedRun(result, farm))
    },
    async apply(body, signal) {
      decode('apply_request', body)
      const result = decode('apply_result', await transport(`${farmPath(body.farm_id)}/runs/${segment(body.run_id)}/apply`, json(body, 'POST', signal)))
      if (result.request_id !== body.request_id) throw new Error('Apply response identity mismatch')
      if (['applied', 'already_applied'].includes(result.status) && !result.applied_at) throw new Error('Apply acknowledgement has no timestamp')
      return result
    },
    async replan(body, signal) {
      decode('replan', body)
      const run = scopedRun(await transport(`${farmPath(body.request.farm_id)}/runs/${segment(body.previous_run_id)}/replan`, {
        ...json(body.request, 'POST', signal), body: JSON.stringify(body),
      }), body.request.farm_id)
      if (run.request.request_id !== body.request.request_id) throw new Error('Replan request identity mismatch')
      return run
    },
  }
}
