import type { Field, FieldContext, Scenario } from '../api/contracts'

/** Product sections share field, season and scenario; URL compatibility is owned by app/routing. */
export const analyticsSections = ['overview', 'water', 'vegetation', 'rotation', 'soil'] as const
export type AnalyticsSection = (typeof analyticsSections)[number]
export type StrategyFocus = 'profiles' | 'economics'

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
  field: Field
  fields: Field[]
  context: FieldContext
  section: AnalyticsSection
  jobId?: string
  forecastDay?: string
  irrigationAvailable: boolean
  chooseField: (fieldId: string) => void
  chooseSeason: (season: number) => void
  chooseScenario: (scenario: Scenario) => void
  onSection: (section: AnalyticsSection) => void
  showJob: (jobId: string, scenario?: Scenario) => void
  openFields: () => void
  openStrategies: () => void
  openAgent: (jobId?: string) => void
  openMapLayer: (layer: 'ndvi_variability' | 'terrain') => void
}
