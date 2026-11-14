import type { Polygon } from '../../api/contracts'
import type { OnboardingInputs } from '../../api/onboarding'
import type { SetupLocation } from './locations'
import type { OnboardingAnswers } from './onboardingAnswers'

export type SetupDraft = {
  location: SetupLocation
  geometry: Polygon | null
  name: string
  answers: OnboardingAnswers
  step: 'map' | 'chat'
}

function readStorage<T>(key: string): T | undefined {
  try {
    const value = localStorage.getItem(key)
    return value ? JSON.parse(value) as T : undefined
  } catch { return undefined }
}

function writeStorage(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* Server drafts are also saved. */ }
}

const locationKey = 'field-shift:last-setup-location'
const fieldKey = 'field-shift:last-active-field'
const pendingKey = 'field-shift:pending-setup'
const draftKey = (id: string) => 'field-shift:setup:' + id

export const hasLocalSetupDraft = (id: string) => Boolean(readStorage<SetupDraft>(draftKey(id)))

export function readSetupDraft(id: string): SetupDraft {
  return readStorage<SetupDraft>(draftKey(id)) ?? {
    location: readStorage<SetupLocation>(locationKey) ?? { countryCode: '', regionCode: '' },
    geometry: null, name: '', answers: {}, step: 'map',
  }
}

export const saveSetupDraft = (id: string, draft: SetupDraft) => writeStorage(draftKey(id), draft)
export function finishSetupDraft(id: string, location: SetupLocation) {
  writeStorage(locationKey, location)
  try {
    localStorage.removeItem(draftKey(id))
    if (pendingSetup() === id) localStorage.removeItem(pendingKey)
  } catch { /* Safe to replay completed setup. */ }
}

export const pendingSetup = () => readStorage<string>(pendingKey)
export const rememberSetup = (id: string) => writeStorage(pendingKey, id)
export const lastSelectedField = () => readStorage<string>(fieldKey)
export const rememberSelectedField = (id: string) => writeStorage(fieldKey, id)

export function draftFromInputs({
  country_code, region_code, name, geometry, history, allowed_crops, irrigation, priority,
}: OnboardingInputs): SetupDraft {
  return {
    location: { countryCode: country_code, regionCode: region_code ?? 'all' },
    name: name ?? '', geometry: geometry ?? null, step: geometry ? 'chat' : 'map',
    answers: { history, allowed_crops, irrigation, priority },
  }
}

export function inputsFromDraft(draft: SetupDraft, startSeason: number): OnboardingInputs {
  return {
    country_code: draft.location.countryCode,
    region_code: draft.location.regionCode,
    geometry: draft.geometry ?? undefined,
    name: draft.name.trim() || undefined,
    start_season: startSeason,
    ...draft.answers,
  }
}
