import { useLocale } from '../shared/locale'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { scenarioOrDefault, type Field, type Scenario } from '../api/contracts'
import type { AnalyticsSection, MapFocus, StrategyFocus } from '../shared/productContext'
import { Assistant, type AssistantScreen } from '../features/assistant/Assistant'
import { FarmPage } from '../features/farm/FarmPage'
import { FieldsPage } from '../features/fields/FieldsPage'
import { Empty, ErrorBox, Loading } from '../shared/ui'
import { useResource } from '../shared/useResource'
import { canonicalRoute, parseRoute, routeAddress, type Page, type Route } from './routing'
import { Shell } from './Shell'
import { NotificationProvider } from '../features/notifications/Notifications'

const StrategiesPage = lazy(() => import('../features/strategies/StrategiesPage').then(m => ({ default: m.StrategiesPage })))
const AnalyticsPage = lazy(() => import('../features/analytics/AnalyticsPage').then(m => ({ default: m.AnalyticsPage })))
const noFields: Field[] = []
type DestinationOptions = {
  fieldId?: string; season?: number; scenario?: Scenario; jobId?: string | null; section?: AnalyticsSection;
  forecastDay?: string; addField?: boolean; layer?: Route['layer']; mapFocus?: MapFocus;
  signal?: Route['signal']; strategyFocus?: StrategyFocus;
}
function loadWorkspace(signal: AbortSignal) {
  return Promise.all([api.fields(signal), api.farm(signal)]).then(([fields, farm]) => ({ fields, farm }))
}
export function App() {
  const { t } = useLocale()
  const [route, setRoute] = useState(() => parseRoute(location.href))
  const [strategiesVisited, setStrategiesVisited] = useState(route.page === 'strategies')
  const workspace = useResource(loadWorkspace)
  const fields = workspace.data?.fields ?? noFields
  const farm = workspace.data?.farm
  const requestedField = fields.find(({ id }) => id === route.fieldId)
  const missingField = route.page !== 'farm' && Boolean(route.fieldId) && !requestedField
  const field = missingField ? undefined : requestedField ?? fields.find(({ id }) => id === 'F-03') ?? fields[0]
  const scenario = scenarioOrDefault(route.scenario)
  const season = route.season ?? (farm ? farm.season + (route.page === 'strategies' ? 1 : 0) : new Date().getUTCFullYear())
  const context = field ? { field_id: field.id, season, scenario } : undefined
  const [assistantOpen, setAssistantOpen] = useState(route.page === 'agent')
  const setAddress = useCallback((next: Route, replace = false) => {
    const address = routeAddress(next)
    if (address !== location.pathname + location.search) {
      history[replace ? 'replaceState' : 'pushState'](null, '', address)
    }
    setRoute(parseRoute(address))
  }, [])
  useEffect(() => {
    const listener = () => setRoute(parseRoute(location.href))
    window.addEventListener('popstate', listener)
    return () => window.removeEventListener('popstate', listener)
  }, [])
  useEffect(() => {
    if (route.page === 'agent') {
      setAssistantOpen(true)
      setAddress({ ...route, page: 'fields' }, true)
    } else {
      // Normalize old bookmarks once, without creating a back-button loop.
      const canonical = routeAddress(route)
      if (location.pathname !== canonical.split('?')[0]) setAddress(route, true)
    }
    if (route.page === 'strategies') setStrategiesVisited(true)
  }, [route, setAddress])
  useEffect(() => {
    const titles: Record<Page, string> = { fields: 'Fields', strategies: 'Strategies', analytics: 'Analytics', agent: 'Assistant', farm: 'Farm management', water: 'Analytics', rotations: 'Analytics' }
    document.title = t(titles[route.page]) + ' · Field Shift'
  }, [route.page, t])

  function go(page: Page, options: DestinationOptions = {}) {
    if (page === 'agent') {
      if (options.fieldId || options.season !== undefined || options.scenario) setAddress({ ...route, fieldId: options.fieldId ?? field?.id, scenario: options.scenario ?? scenario, season: options.season ?? season })
      setAssistantOpen(true)
      return
    }
    const target = canonicalRoute({ page, section: options.section })
    const sameWater = target.page === 'analytics' && (target.section ?? options.section) === 'water'
      && route.page === 'analytics' && route.section === 'water'
      && (!options.fieldId || options.fieldId === field?.id)
      && (!options.scenario || options.scenario === scenario)
    const nextSeason = options.season ?? route.season ?? (farm ? farm.season + (target.page === 'strategies' ? 1 : 0) : season)
    setAddress({ page: target.page, fieldId: options.fieldId ?? field?.id, season: nextSeason,
      scenario: options.scenario ?? scenario, trajectory: route.trajectory,
      section: target.page === 'analytics' ? options.section ?? target.section ?? 'overview' : undefined,
      jobId: options.jobId === null ? undefined : options.jobId ?? (sameWater ? route.jobId : undefined),
      forecastDay: options.forecastDay, addField: options.addField, layer: options.layer,
      mapFocus: options.mapFocus, strategyFocus: options.strategyFocus })
    if (target.page === 'strategies') setStrategiesVisited(true)
    if (target.page !== route.page) window.scrollTo(0, 0)
  }
  function openFieldForecast(fieldId: string, forecastDay?: string) {
    go('analytics', { fieldId, section: 'water', forecastDay, jobId: null })
  }
  function chooseField(fieldId: string) { setAddress({ ...route, fieldId, scenario }) }
  function chooseScenario(value: Scenario) { setAddress({ ...route, fieldId: field?.id, scenario: value }) }
  function showWaterJob(jobId: string, value?: Scenario) {
    if (routeAddress(route) !== routeAddress(parseRoute(location.href))) return
    go('analytics', { section: 'water', jobId, scenario: value })
  }
  function openMapLayer(layer: 'ndvi_variability' | 'terrain', focus?: MapFocus) {
    go('fields', { layer, mapFocus: focus, season: focus?.season ?? season })
  }
  function openAssistantScreen(screen: AssistantScreen) {
    // Old host action IDs retain their analytical meaning; new IDs expose strategy decisions.
    if (screen === 'water') go('analytics', { section: 'water' })
    else if (screen === 'rotations') go('analytics', { section: 'rotation' })
    else go(screen)
  }
  function createField(fieldId: string) {
    if (parseRoute(location.href).page === 'fields') go('fields', { fieldId })
    workspace.reload()
  }
  function updateField(updated: Field) {
    workspace.update(({ fields, farm }) => {
      const previous = fields.find(({ id }) => id === updated.id)
      return previous ? { fields: fields.map(item => item.id === updated.id ? updated : item),
        farm: { ...farm, total_area_ha: farm.total_area_ha + updated.area_ha - previous.area_ha } } : { fields, farm }
    })
  }

  let content
  if (workspace.pending) content = <Loading text={t('Loading workspace…')} />
  else if (workspace.error) content = <ErrorBox message={workspace.error} retry={workspace.reload} />
  else if (farm) {
    if (missingField) content = <Empty><p>{t('The linked field is no longer available.')}</p><button className="button" type="button" onClick={() => go('fields')}>{t('Open Fields')}</button></Empty>
    else if (route.page === 'fields' || route.page === 'agent') content = (
      <FieldsPage key={season} fields={fields} selected={field} season={season} initialDrawing={route.addField}
        initialAnalysis={route.analysis} showNdviLayer={route.layer === 'ndvi_variability'} showTerrainLayer={route.layer === 'terrain'} mapFocus={route.mapFocus}
        chooseField={chooseField} onCreate={createField} onUpdate={updateField} reloadFields={workspace.reload} openForecast={openFieldForecast}
        openStrategies={() => go('strategies', { season: Math.max(season, farm.season + 1) })}
        openAnalytics={(section = 'overview') => go('analytics', { section })} openAgent={() => setAssistantOpen(true)} />
    )
    else if (route.page === 'farm') content = <FarmPage farm={farm} fields={fields} refresh={workspace.reload}
      openVegetation={fieldId => go('analytics', { fieldId, section: 'vegetation' })}
      openStrategies={() => go('strategies')}
      onDefaultsSaved={defaults => workspace.update(current => ({ ...current, farm: { ...current.farm, defaults } }))}
      onObservationSaved={observation => workspace.update(current => ({ ...current,
        farm: { ...current.farm, observations: [observation, ...current.farm.observations.filter(({ id }) => id !== observation.id)] } }))}
      onAddField={() => go('fields', { addField: true })} openField={fieldId => go('fields', { fieldId })} openWater={openFieldForecast} />
    else if (!context || !field) content = <Empty><p>{t('Add a field to use this workspace.')}</p><button className="button" type="button" onClick={() => go('fields', { addField: true })}>{t('Add field')}</button></Empty>
    else if (route.page === 'analytics') content = <Suspense fallback={<Loading text={t('Loading workspace…')} />}>
      <AnalyticsPage fields={fields} field={field} context={context} section={route.section ?? 'overview'} jobId={route.jobId} forecastDay={route.forecastDay}
        irrigationAvailable={farm.defaults.irrigation_available} chooseField={chooseField} chooseSeason={year => setAddress({ ...route, season: year })}
        chooseScenario={chooseScenario} onSection={section => go('analytics', { section })} showJob={showWaterJob}
        openFields={() => go('fields')} openStrategies={() => go('strategies')}
        openAgent={jobId => { if (jobId) showWaterJob(jobId); setAssistantOpen(true) }} openMapLayer={openMapLayer} />
    </Suspense>
  }
  const keepStrategies = farm && field && !missingField && (strategiesVisited || route.page === 'strategies')
  return <NotificationProvider fields={fields} season={farm?.season ?? new Date().getUTCFullYear()} farmKey={farm?.name ?? 'workspace'}
    openAgent={item => { go('agent', { fieldId: item.fieldId, season: farm?.season, scenario: 'baseline', signal: item.kind === 'water' ? 'water' : 'vegetation' }) }}
    openAnalysis={item => item.kind === 'water'
      ? go('analytics', { fieldId: item.fieldId, season: farm?.season, section: 'water', forecastDay: item.date, jobId: null })
      : go('analytics', { fieldId: item.fieldId, season: farm?.season, section: 'vegetation' })}>
    <Shell route={route} farmName={farm?.name} navigate={go} assistantOpen={assistantOpen} openAssistant={() => setAssistantOpen(true)}>
      {content}
      {keepStrategies && <div hidden={route.page !== 'strategies'} inert={route.page !== 'strategies'} className="strategies-outlet">
        <Suspense fallback={<Loading text={t('Loading workspace…')} />}><StrategiesPage field={field} fields={fields} season={season}
          trajectory={route.trajectory} focus={route.strategyFocus}
          onContext={(fieldId, year, trajectory) => setAddress({ ...route, page: 'strategies', fieldId, season: year, trajectory })}
          openAnalytics={(section = 'overview') => go('analytics', { section })} openAgent={() => setAssistantOpen(true)} /></Suspense>
      </div>}
    </Shell>
    {farm && <Assistant field={missingField ? undefined : field} season={season} scenario={scenario} open={assistantOpen} onOpenChange={setAssistantOpen}
      openScreen={openAssistantScreen} openVegetation={ndvi => ndvi ? openMapLayer('ndvi_variability') : go('analytics', { section: 'vegetation' })}
      openForecast={day => { if (field) openFieldForecast(field.id, day) }} />}
  </NotificationProvider>
}
