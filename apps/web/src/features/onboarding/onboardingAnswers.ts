import type { PlanningCrop } from '../../api/rotationPlanning'

export const onboardingCrops: PlanningCrop[] = ['Wheat', 'Maize', 'Soy', 'Barley', 'Sunflower', 'Rapeseed', 'Peas']
export type OnboardingPriority = 'water' | 'soil' | 'low_change'
export type OnboardingAnswers = {
  history?: { season: number; crop: string | null }[]
  allowed_crops?: string[]
  irrigation?: boolean | null
  priority?: OnboardingPriority
}

const aliases: Record<PlanningCrop, string[]> = {
  Wheat: ['wheat', 'пшениця', 'пшеницю'],
  Maize: ['maize', 'corn', 'кукурудза', 'кукурудзу'],
  Soy: ['soy', 'soya', 'soybean', 'soybeans', 'соя', 'сою'],
  Barley: ['barley', 'ячмінь'],
  Sunflower: ['sunflower', 'sunflowers', 'соняшник', 'соняшнику'],
  Rapeseed: ['rapeseed', 'canola', 'ріпак', 'ріпаку'],
  Peas: ['peas', 'pea', 'горох', 'гороху'],
}

function normalize(value: string) {
  return value.trim().toLowerCase().replace(/[’`]/g, "'").replace(/\s+/g, ' ').replace(/[.!?]+$/, '')
}

export function parseCropAnswer(text: string): { exclude: boolean; crops: PlanningCrop[] } | undefined {
  const normalized = normalize(text)
  const negative = /^(?:без |no |without |exclude |i (?:don't|do not) grow |не вирощую )|(?: не вирощую)$/
  const names = normalized.replace(negative, '').replace(/^(?:вирощую |i grow )/, '')
    .split(/\s+(?:і|й|та|and)\s+|\s*[,;]\s*/)
  const crops = new Set<PlanningCrop>()
  for (const name of names) {
    const crop = onboardingCrops.find((item) => aliases[item].includes(name))
    if (!crop) return undefined
    crops.add(crop)
  }
  return { exclude: negative.test(normalized), crops: [...crops] }
}

export function parseHistoryAnswer(text: string) {
  if (['невідомо', "не пам'ятаю", 'не знаю', 'unknown', "i don't know", "i don't remember"].includes(normalize(text))) return null
  const answer = parseCropAnswer(text)
  if (answer && !answer.exclude && answer.crops.length === 1) return answer.crops[0]
  return undefined
}

export function parseIrrigationAnswer(text: string): boolean | null | undefined {
  const normalized = normalize(text)
  if (['так', 'є', 'є полив', 'yes', 'yes i can', 'irrigated'].includes(normalized)) return true
  if (['ні', 'без поливу', 'немає', 'немає поливу', 'no', 'rainfed'].includes(normalized)) return false
  if (['невідомо', 'не знаю', 'не впевнена', 'не впевнений', 'unknown', 'not sure'].includes(normalized)) return null
  return undefined
}
