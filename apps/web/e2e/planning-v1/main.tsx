/** Isolated dev/test entry. Never imported from App or a production feature. */
import { createRoot } from 'react-dom/client'
import { useState } from 'react'
import { PlanningWorkspace } from '../../src/features/planning/PlanningWorkspace'
import { createPlanningApi, type PlanningTransport } from '../../src/api/planningV1'
import { setAccessCsrf } from '../../src/api/client'
import type { ApplyPlanRequest, FarmPlanningRequest, PlanningRun, ProfileWriteRequest, ReplanRequest } from '../../src/api/planningV1.generated'
import { fixture, now } from '../../tests/planning/fixtures'
import '../../src/shared/styles.css'

const params = new URLSearchParams(location.search)
const data = fixture(params.get('case') ?? 'G23')
let savedRun = structuredClone(data.run)
if (params.get('state') && ['queued', 'running', 'failed', 'cancelled'].includes(params.get('state')!)) {
  savedRun.status = params.get('state') as PlanningRun['status']
  savedRun.comparison = null
  if (savedRun.status === 'failed') savedRun.error = data.run.comparison!.diagnostics[0]
}
if (params.has('stale')) data.bundle.request.weather.stale_after = '2026-11-14T01:00:00Z'
if (params.has('stale')) savedRun.request.weather.stale_after = '2026-11-14T01:00:00Z'
let storedProfile = structuredClone(data.bundle.field_profile)
const transport: PlanningTransport = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  if (params.has('offline')) throw new Error('Backend unavailable — no replacement data')
  if (options?.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  const body = options?.body ? JSON.parse(String(options.body)) : null
  if (path.endsWith('/profile')) {
    if (body) storedProfile = (body as ProfileWriteRequest).profile
    return structuredClone(storedProfile) as T
  }
  if (path.endsWith('/apply')) {
    const input = body as ApplyPlanRequest
    return { schema_version: '1.0.0', request_id: input.request_id, status: params.get('apply') ?? 'applied',
      applied_at: params.has('apply') ? null : now().toISOString(), reasons: params.has('apply') ? data.run.comparison!.diagnostics : [] } as T
  }
  if (options?.method === 'POST') {
    const request = path.endsWith('/replan') ? (body as ReplanRequest).request : body as FarmPlanningRequest
    savedRun = { ...structuredClone(data.run), run_id: `run-${request.request_id}`, request }
    savedRun.comparison!.request_id = request.request_id
    if (path.endsWith('/replan')) {
      savedRun.comparison!.alternatives[0].assignments[1].crop = data.crops[1]
    }
    return structuredClone(savedRun) as T
  }
  if (path.endsWith('/runs')) return [structuredClone(savedRun)] as T
  return structuredClone(savedRun) as T
}
setAccessCsrf('synthetic-e2e-csrf')
const api = params.has('http') ? createPlanningApi() : createPlanningApi(transport)
const evidence = async (_run: PlanningRun, signal: AbortSignal) => {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  return structuredClone(data.evidence)
}
function Harness() {
  const [context, setContext] = useState({ fieldId: data.bundle.field_profile.field_id, season: 2027, trajectoryId: 'reference' })
  if (params.has('phone') && !params.has('inside')) {
    const url = new URL(location.href); url.searchParams.set('inside', '1')
    return <iframe title="Planning phone preview" src={url.href} style={{ width: 390, height: 844, maxWidth: '100%', border: '1px solid #aaa', borderRadius: 30, display: 'block', margin: '20px auto' }} />
  }
  return <PlanningWorkspace initialRequest={data.bundle.request} initialRun={params.has('empty') ? undefined : savedRun} cropOptions={data.crops}
    context={context} onContextChange={setContext} locale={params.get('locale') === 'uk' ? 'uk' : 'en'} api={api} loadEvidence={evidence} clock={now} />
}
createRoot(document.getElementById('root')!).render(<Harness />)
