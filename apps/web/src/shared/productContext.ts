import type { Field, FieldContext } from '../api/contracts'
import type { SpectralIndex } from '../api/analytics'

/** Product sections share field, season and scenario; URL compatibility is owned by app/routing. */
export const analyticsSections = ['overview', 'water', 'vegetation', 'soil'] as const
export type AnalyticsSection = (typeof analyticsSections)[number]
export type StrategyFocus = 'profiles' | 'economics' | 'native'

export type MapFocus = {
  season?: number
  observationDate?: string
  comparisonDate?: string
  index?: SpectralIndex
  comparison?: boolean
}

export interface StrategiesPageProps {
  field: Field
  fields: Field[]
  season: number
  trajectory?: string
  focus?: StrategyFocus
  onContext: (fieldId: string, season: number, trajectory: string) => void
  openAnalytics: (section?: AnalyticsSection) => void
  openAgent: () => void
}

export interface AnalyticsPageProps {
  field?: Field
  fields: Field[]
  context?: FieldContext
  jobId?: string
  forecastDay?: string
  chooseField: (fieldId: string) => void
  addField: () => void
  openAgent: (jobId?: string) => void
}
