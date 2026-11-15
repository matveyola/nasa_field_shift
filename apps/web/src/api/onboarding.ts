import { ApiError, request } from './client'
import type { Field, Polygon } from './contracts'
import type { OnboardingAnswers } from '../features/onboarding/onboardingAnswers'

export type OnboardingInputs = OnboardingAnswers & {
  country_code: string
  region_code: string
  name?: string
  geometry?: Polygon
  start_season: number
}

export type OnboardingSetup = {
  id: string
  status: 'draft' | 'completed'
  inputs: OnboardingInputs
  field: Field | null
  demo_plan: {
    demo: true
    source: 'onboarding_demo_v1'
    start_season: number
    crops: { year: number; crop: string }[]
    alternatives?: { year: number; crop: string }[][]
  } | null
}

async function findSetup(path: string, signal?: AbortSignal) {
  try { return await request<OnboardingSetup>(path, { signal }) }
  catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export const onboardingApi = {
  get: (id: string, signal: AbortSignal) => findSetup('/onboarding/' + id, signal),
  field: (id: string, signal: AbortSignal) => findSetup('/onboarding/fields/' + encodeURIComponent(id), signal),
  save: (id: string, body: OnboardingInputs) => request<OnboardingSetup>('/onboarding/' + id, {
    method: 'PUT', body: JSON.stringify(body),
  }),
  complete: (id: string) => request<OnboardingSetup>('/onboarding/' + id + '/complete', { method: 'POST' }),
}
