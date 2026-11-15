import type { Field, WaterForecast } from '../../api/contracts'
import { getCurrentWaterOutlook } from '../water/waterOutlook.ts'
import { waterMetrics } from './waterMetrics.ts'

/** One eligibility path for the current summary and its detailed calculation. */
export function currentWater(field: Field, forecast: WaterForecast | null | undefined, today: string) {
  const supported = ['maize', 'corn', 'soy', 'soybean', 'soybeans', 'wheat', 'spring wheat', 'barley', 'sunflower', 'rapeseed', 'canola', 'peas', 'dry peas'].includes(field.crop.trim().toLowerCase())
  const scopedForecast = supported && forecast?.crop.trim().toLowerCase() === field.crop.trim().toLowerCase() ? forecast : undefined
  const outlook = getCurrentWaterOutlook(scopedForecast, field.id, today)
  const current = outlook.kind !== 'unavailable' ? forecast : undefined
  const days = outlook.kind !== 'unavailable' ? outlook.days : []
  return { outlook, current, days, first: days[0], metrics: current ? waterMetrics(days, current.threshold_mm) : null }
}
