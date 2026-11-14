import type {
  AgentReply, AgentStatus, Conversation, ConversationDetail, Farm, FarmDefaults, Field, FieldContext, Job, JobKind, JobOptions, MoistureForecast, MoistureLayer, MoistureSource,
  NewField, NewObservation, Observation, Polygon, Position, Rotations, RotationYield, SatelliteImage, SatelliteScene, WaterForecast, WaterResult,
} from './contracts'

export const apiBase = import.meta.env.VITE_API_BASE ?? '/api'
let csrfToken: string | null = null
export function setAccessCsrf(token: string | null) { csrfToken = token }
/** Headers every state-changing request carries (same-origin marker and the session's CSRF token). */
export function applicationHeaders(): Record<string, string> {
  return { 'X-Field-Shift-Request': '1', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) }
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(options?.method?.toUpperCase() ?? 'GET')
  const response = await fetch(apiBase + path, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(unsafe ? applicationHeaders() : {}),
      ...(options?.body ? { 'Content-Type': 'application/json' } : {}),
      ...options?.headers,
    },
  })
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('field-shift-session-expired'))
    const error = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = error?.detail
    const validation = Array.isArray(detail)
      ? detail.find((item): item is { msg: string } => Boolean(item) && typeof item.msg === 'string')
      : undefined
    const message = typeof detail === 'string' ? detail
      : validation?.msg ?? 'Request failed (' + response.status + ').'
    throw new ApiError(response.status, message)
  }
  return await response.json() as T
}

function contextQuery({ field_id, season, scenario }: FieldContext) {
  return new URLSearchParams({ field_id, season: String(season), scenario }).toString()
}

export const api = {
  fields: (signal?: AbortSignal) => request<Field[]>('/fields', { signal }),
  createField: (body: NewField) =>
    request<Field>('/fields', { method: 'POST', body: JSON.stringify(body) }),
  saveBoundary: (fieldId: string, body: { geometry: Polygon; boundary_revision: number; effective_season?: number }) =>
    request<Field>('/fields/' + encodeURIComponent(fieldId) + '/boundary', {
      method: 'PATCH', body: JSON.stringify(body),
    }),
  savePlanting: (fieldId: string, season: number, body: { geometry: Polygon | null; boundary_revision: number }) =>
    request<Field>('/fields/' + encodeURIComponent(fieldId) + '/planting/' + season, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  satellite: (fieldId: string, cutoff: string, signal: AbortSignal) => {
    const query = cutoff ? '?' + new URLSearchParams({ on_or_before: cutoff }) : ''
    return request<SatelliteScene | null>('/fields/' + encodeURIComponent(fieldId) + '/satellite' + query, { signal })
  },
  satelliteAt: ([longitude, latitude]: Position, signal: AbortSignal) => {
    const query = new URLSearchParams({ longitude: String(longitude), latitude: String(latitude) })
    return request<SatelliteImage | null>('/fields/satellite?' + query, { signal })
  },
  moisture: (fieldId: string, source: MoistureSource, season: number | undefined, sampleDate: string, signal: AbortSignal) => {
    const query = new URLSearchParams({ source })
    if (season !== undefined) query.set('season', String(season))
    if (sampleDate) query.set('sample_date', sampleDate)
    return request<MoistureLayer | null>('/fields/' + encodeURIComponent(fieldId) + '/moisture?' + query, { signal })
  },
  moistureForecast: (fieldId: string, signal: AbortSignal) =>
    request<MoistureForecast | null>('/fields/' + encodeURIComponent(fieldId) + '/moisture-forecast', { signal }),
  farm: (signal?: AbortSignal) => request<Farm>('/farm', { signal }),
  saveDefaults: (body: FarmDefaults) =>
    request<FarmDefaults>('/farm/defaults', { method: 'PATCH', body: JSON.stringify(body) }),
  addObservation: (body: NewObservation) =>
    request<Observation>('/farm/observations', { method: 'POST', body: JSON.stringify(body) }),
  observationPhoto: (id: number) => `${apiBase}/farm/observations/${id}/photo`,
  water: (context: FieldContext, signal?: AbortSignal) =>
    request<WaterResult>('/water?' + contextQuery(context), { signal }),
  waterForecast: (fieldId: string, signal?: AbortSignal) =>
    request<WaterForecast | null>('/water/forecast?' + new URLSearchParams({ field_id: fieldId }), { signal }),
  rotations: (context: FieldContext, signal?: AbortSignal) =>
    request<Rotations>('/rotations?' + contextQuery(context), { signal }),
  rotationYield: (fieldId: string, season: number, signal?: AbortSignal) =>
    request<RotationYield>('/rotations/yield?' + new URLSearchParams({ field_id: fieldId, season: String(season) }), { signal }),
  setMainPlan: (body: { field_id: string; season: number; plan_id: string }) =>
    request<typeof body>('/rotations/main', { method: 'PATCH', body: JSON.stringify(body) }),
  savePlan: (id: string, body: { field_id: string; season: number; crops: string[] }) =>
    request<typeof body>('/rotations/plans/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(body) }),
  conversations: (fieldId: string, signal?: AbortSignal) =>
    request<Conversation[]>('/agent/conversations?field_id=' + encodeURIComponent(fieldId), { signal }),
  conversation: (id: string, signal?: AbortSignal) =>
    request<ConversationDetail>('/agent/conversations/' + encodeURIComponent(id), { signal }),
  agentStatus: (signal?: AbortSignal) => request<AgentStatus>('/agent/status', { signal }),
  chat: (body: FieldContext & { message: string; locale?: 'en' | 'uk'; run_id?: string; conversation_id?: string }, signal?: AbortSignal) =>
    request<AgentReply>('/agent/chat', { method: 'POST', body: JSON.stringify(body), signal }),
  createJob: (kind: JobKind, context: FieldContext, key: string, irrigationMm = 0, options: JobOptions = {}) =>
    request<Job>('/jobs', {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify({
        kind,
        field_id: context.field_id,
        inputs: { season: context.season, scenario: context.scenario, irrigation_mm: irrigationMm, ...options },
      }),
    }),
  job: (id: string, signal?: AbortSignal) =>
    request<Job>('/jobs/' + encodeURIComponent(id), { signal }),
  jobs: (fieldId: string, signal?: AbortSignal) =>
    request<Job[]>('/jobs?field_id=' + encodeURIComponent(fieldId), { signal }),
}
