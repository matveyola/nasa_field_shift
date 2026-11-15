import type { Field, FieldContext, Scenario } from '../../src/api/contracts'
import type { MapFocus } from '../../src/shared/productContext'
export type { MapFocus } from '../../src/shared/productContext'

/** Section names for the isolated legacy browser fixture. */
export const analyticsSections = ['overview', 'water', 'vegetation', 'rotation', 'soil'] as const
export type AnalyticsSection = (typeof analyticsSections)[number]

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
  openMapLayer: (layer: 'ndvi_variability' | 'terrain', focus?: MapFocus) => void
}
