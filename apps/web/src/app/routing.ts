import type { AnalyticsSection, MapFocus, StrategyFocus } from '../shared/productContext'
import { analyticsSections } from '../shared/productContext.ts'

export const pages = ['fields', 'strategies', 'agent', 'analytics', 'farm'] as const
export type ProductPage = (typeof pages)[number]
/** Legacy names remain valid host intents; generated links always use product destinations. */
export type Page = ProductPage | 'water' | 'rotations'
export type Route = {
  page: Page
  home?: boolean
  setupId?: string
  planning?: boolean
  section?: AnalyticsSection
  strategyFocus?: StrategyFocus
  season?: number
  trajectory?: string
  fieldId?: string
  scenario?: string
  jobId?: string
  addField?: boolean
  forecastDay?: string
  analysis?: boolean
  layer?: 'ndvi_variability' | 'terrain'
  mapFocus?: MapFocus
  signal?: 'water' | 'vegetation'
}
const spectralIndices = ['ndvi', 'msavi', 'ndre', 'reci', 'mcari', 'ndmi', 'ndwi'] as const
function isDay(value: string | null | undefined): value is string {
  return !!value && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 10) === value
}
function validSeason(value: string | null): number | undefined {
  return value && /^\d{4}$/.test(value) && +value >= 1900 && +value <= 2200 ? +value : undefined
}
function identifier(value?: string | null): string | undefined {
  return value && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value) ? value : undefined
}
export function canonicalRoute(route: Route): Route {
  if (route.page === 'water') return { ...route, page: 'analytics', section: 'water' }
  if (route.page === 'rotations') return route.planning
    ? { ...route, page: 'strategies', planning: undefined, section: undefined }
    : { ...route, page: 'analytics', section: 'rotation' }
  return route
}
export function parseRoute(address: string): Route {
  const url = new URL(address, 'https://field-shift.local')
  const path = url.pathname.replace(/^\/+|\/+$/g, '')
  const explicit = path === 'onboarding' || path === 'water' || path === 'rotations' || pages.some(p => p === path)
  const setup = path === 'onboarding' ? url.searchParams.get('setup') : null
  const setupId = setup && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(setup) ? setup : undefined
  const legacy = path === 'water' || path === 'rotations' ? path : undefined
  const hasMap = url.searchParams.get('layer') === 'ndvi_variability' || url.searchParams.get('layer') === 'terrain'
  const oldVegetation = path === 'fields' && url.searchParams.get('analysis') === 'vegetation' && !hasMap
  const initial: Route = {
    page: legacy ?? (pages.find(p => p === path) ?? (path === 'onboarding' ? 'fields' : 'strategies')),
    ...(url.searchParams.get('planning') === '1' && legacy === 'rotations' ? { planning: true } : {}),
  }
  const target = canonicalRoute(initial)
  if (oldVegetation) { target.page = 'analytics'; target.section = 'vegetation' }
  if (target.page === 'analytics' && !target.section) {
    target.section = analyticsSections.find(s => s === url.searchParams.get('section')) ?? 'overview'
  }
  const route: Route = { page: target.page, fieldId: url.searchParams.get('field') || undefined,
    scenario: url.searchParams.get('scenario') || undefined,
    jobId: target.page === 'agent' || (target.page === 'analytics' && target.section === 'water')
      ? url.searchParams.get('job') || undefined : undefined,
    ...(target.section ? { section: target.section } : {}),
  }
  if (!explicit) route.home = true
  if (setupId) route.setupId = setupId
  const season = validSeason(url.searchParams.get('season'))
  if (season !== undefined) route.season = season
  const trajectory = identifier(url.searchParams.get('trajectory'))
  if (trajectory) route.trajectory = trajectory
  if (route.page === 'strategies' && ['profiles', 'economics'].includes(url.searchParams.get('setup') ?? '')) {
    route.strategyFocus = url.searchParams.get('setup') as StrategyFocus
  }
  if (route.page === 'fields' && hasMap) {
    route.layer = url.searchParams.get('layer') as Route['layer']
    route.analysis = true
    const focus: MapFocus = {}
    if (isDay(url.searchParams.get('observation'))) focus.observationDate = url.searchParams.get('observation')!
    if (isDay(url.searchParams.get('comparison'))) focus.comparisonDate = url.searchParams.get('comparison')!
    const index = spectralIndices.find(i => i === url.searchParams.get('index'))
    if (index) focus.index = index
    if (url.searchParams.get('compare') === '1') focus.comparison = true
    if (Object.keys(focus).length) route.mapFocus = focus
  }
  if (route.page === 'fields' && url.searchParams.get('add') === '1') route.addField = true
  if (route.page === 'analytics' && route.section === 'water' && !route.jobId && isDay(url.searchParams.get('day'))) {
    route.forecastDay = url.searchParams.get('day')!
  }
  if (route.page === 'agent' && ['water', 'vegetation'].includes(url.searchParams.get('signal') ?? '')) {
    route.signal = url.searchParams.get('signal') as Route['signal']
  }
  return route
}
export function resolveHomeRoute(route: Route, hasFields: boolean): Route {
  return route.home ? { ...route, page: hasFields ? 'strategies' : 'fields' } : route
}
export function routeAddress(intent: Route): string {
  const route = canonicalRoute(intent)
  const params = new URLSearchParams()
  if (route.page === 'analytics') params.set('section', route.section ?? 'overview')
  if (route.setupId) params.set('setup', route.setupId)
  if (route.page === 'strategies' && route.strategyFocus) params.set('setup', route.strategyFocus)
  if (route.season !== undefined && Number.isInteger(route.season) && route.season >= 1900 && route.season <= 2200) params.set('season', String(route.season))
  if (identifier(route.trajectory)) params.set('trajectory', route.trajectory!)
  if (route.fieldId) params.set('field', route.fieldId)
  if (route.scenario) params.set('scenario', route.scenario)
  if (route.jobId && (route.page === 'agent' || (route.page === 'analytics' && route.section === 'water'))) params.set('job', route.jobId)
  if (route.page === 'agent' && route.signal) params.set('signal', route.signal)
  if (route.page === 'analytics' && route.section === 'water' && !route.jobId && isDay(route.forecastDay)) params.set('day', route.forecastDay)
  if (route.page === 'fields') {
    if (route.addField) params.set('add', '1')
    const layer = route.layer
    if (layer) {
      params.set('layer', layer)
      if (isDay(route.mapFocus?.observationDate)) params.set('observation', route.mapFocus!.observationDate!)
      if (isDay(route.mapFocus?.comparisonDate)) params.set('comparison', route.mapFocus!.comparisonDate!)
      if (route.mapFocus?.index && spectralIndices.includes(route.mapFocus.index)) params.set('index', route.mapFocus.index)
      if (route.mapFocus?.comparison) params.set('compare', '1')
    } else if (route.analysis) return routeAddress({ ...route, page: 'analytics', section: 'vegetation' })
  }
  return '/' + (route.setupId ? 'onboarding' : route.home ? '' : route.page) + (params.size ? '?' + params : '')
}
