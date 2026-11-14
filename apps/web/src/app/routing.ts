export const pages = ['fields', 'water', 'agent', 'rotations', 'farm'] as const
export type Page = (typeof pages)[number]
export type Route = {
  page: Page
  planning?: boolean
  season?: number
  trajectory?: string
  fieldId?: string
  scenario?: string
  jobId?: string
  addField?: boolean
  forecastDay?: string
  analysis?: boolean
  layer?: 'ndvi_variability'
  signal?: 'water' | 'vegetation'
}

function isForecastDay(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(value)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function parseRoute(address: string): Route {
  const url = new URL(address, 'https://field-shift.local')
  const path = url.pathname.replace(/^\/+|\/+$/g, '')
  const page = pages.find((item) => item === path) ?? 'fields'
  const fieldId = url.searchParams.get('field') || undefined
  const scenario = url.searchParams.get('scenario') || undefined
  const jobId = page === 'water' || page === 'agent' ? url.searchParams.get('job') || undefined : undefined
  const forecastDay = url.searchParams.get('day')
  const analysis = page === 'fields' && url.searchParams.get('analysis') === 'vegetation'
  const signal = url.searchParams.get('signal')
  return {
    page, fieldId, scenario, jobId,
    ...(page === 'rotations' && url.searchParams.get('planning') === '1' ? { planning: true } : {}),
    ...(/^\d{4}$/.test(url.searchParams.get('season') ?? '') && Number(url.searchParams.get('season')) >= 1900 && Number(url.searchParams.get('season')) <= 2200 ? { season: Number(url.searchParams.get('season')) } : {}),
    ...(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(url.searchParams.get('trajectory') ?? '') ? { trajectory: url.searchParams.get('trajectory')! } : {}),
    ...(page === 'fields' && url.searchParams.get('add') === '1' ? { addField: true } : {}),
    ...(analysis ? { analysis: true } : {}),
    ...(analysis && url.searchParams.get('layer') === 'ndvi_variability' ? { layer: 'ndvi_variability' } : {}),
    ...(page === 'water' && !jobId && isForecastDay(forecastDay) ? { forecastDay } : {}),
    ...(page === 'agent' && (signal === 'water' || signal === 'vegetation') ? { signal } : {}),
  }
}

export function routeAddress(route: Route): string {
  const params = new URLSearchParams()
  if (route.planning && route.page === 'rotations') params.set('planning', '1')
  if (route.season !== undefined && Number.isInteger(route.season) && route.season >= 1900 && route.season <= 2200) params.set('season', String(route.season))
  if (route.trajectory && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(route.trajectory)) params.set('trajectory', route.trajectory)
  if (route.fieldId) params.set('field', route.fieldId)
  if (route.scenario) params.set('scenario', route.scenario)
  if (route.jobId && (route.page === 'water' || route.page === 'agent')) params.set('job', route.jobId)
  if (route.page === 'agent' && route.signal) params.set('signal', route.signal)
  if (route.page === 'water' && !route.jobId && isForecastDay(route.forecastDay)) params.set('day', route.forecastDay)
  if (route.addField && route.page === 'fields') params.set('add', '1')
  if (route.analysis && route.page === 'fields') {
    params.set('analysis', 'vegetation')
    if (route.layer === 'ndvi_variability') params.set('layer', route.layer)
  }
  const search = params.toString()
  return '/' + route.page + (search ? '?' + search : '')
}
