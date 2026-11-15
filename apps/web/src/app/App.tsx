import { useLocale } from '../shared/locale'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { scenarioOrDefault, type Field, type Scenario } from '../api/contracts'
import type { AnalyticsSection, MapFocus, StrategyFocus } from '../shared/productContext'
import { Assistant, type AssistantScreen } from '../features/assistant/Assistant'
import { currentAssistantRun, type AssistantRun } from '../features/assistant/assistantRun'
import { FarmPage } from '../features/farm/FarmPage'
import { FieldsPage } from '../features/fields/FieldsPage'
import { Empty, ErrorBox, Loading } from '../shared/ui'
import { useResource } from '../shared/useResource'
import { canonicalRoute, resolveHomeRoute, parseRoute, routeAddress, type Page, type Route } from './routing'
import { Shell } from './Shell'
import { NotificationProvider } from '../features/notifications/Notifications'

import { useAccess } from '../features/access/AccessProvider'
import { lastSelectedField, pendingSetup, rememberSelectedField, rememberSetup } from '../features/onboarding/setupStorage'
const FieldOnboarding = lazy(() => import('../features/onboarding/FieldOnboarding').then(m => ({ default: m.FieldOnboarding })))

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
  const { t, language } = useLocale()
  const { canEdit } = useAccess()
  const [addressRoute, setRoute] = useState(() => parseRoute(location.href))
  const [strategiesVisited, setStrategiesVisited] = useState(addressRoute.page === 'strategies')
  const workspace = useResource(loadWorkspace)
  const fields = workspace.data?.fields ?? noFields
  const farm = workspace.data?.farm
  const route = resolveHomeRoute(addressRoute, fields.length > 0)
  const [lastField, setLastField] = useState(lastSelectedField)
  const [setupInProgress, setSetupInProgress] = useState(pendingSetup)
  const requestedField = fields.find(({ id }) => id === route.fieldId)
  const missingField = !route.home && route.page !== 'farm' && Boolean(route.fieldId) && !requestedField
  const selectedField = missingField ? undefined : requestedField ?? fields.find(({ id }) => id === lastField)
  const field = route.page === 'strategies' || route.page === 'analytics' || missingField
    ? selectedField : selectedField ?? fields[0]
  const scenario = scenarioOrDefault(route.scenario)
  const season = route.season ?? (farm ? farm.season + (route.page === 'strategies' ? 1 : 0) : new Date().getUTCFullYear())
  const context = field ? { field_id: field.id, season, scenario } : undefined
  const [assistantOpen, setAssistantOpen] = useState(route.page === 'agent')
  const [assistantRun, setAssistantRun] = useState<AssistantRun>()
  const assistantRunId = currentAssistantRun(assistantRun, {
    fieldId: missingField ? undefined : field?.id, season, scenario,
    page: route.page, section: route.section, jobId: route.jobId,
  })
  useEffect(() => {
    if (assistantRun && !assistantRunId) setAssistantRun(undefined)
  }, [assistantRun, assistantRunId])
  const setAddress = useCallback((next: Route, replace = false) => {
    const address = routeAddress(next)
    if (address !== location.pathname + location.search) {
      history[replace ? 'replaceState' : 'pushState'](null, '', address)
    }
    setRoute(parseRoute(address))
  }, [])
  useEffect(() => {
    if (requestedField) { setLastField(requestedField.id); rememberSelectedField(requestedField.id) }
  }, [requestedField?.id])
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
      const oldAnalyticsRotation = route.page === 'analytics' && new URLSearchParams(location.search).get('section') === 'rotation'
      if (location.pathname !== canonical.split('?')[0] || oldAnalyticsRotation) setAddress(route, true)
    }
    if (route.page === 'strategies') setStrategiesVisited(true)
  }, [addressRoute, fields.length, setAddress])
  useEffect(() => {
    const titles: Record<Page, string> = { fields: 'Fields', strategies: 'Strategies', analytics: 'Analytics', agent: 'Assistant', farm: 'Farm management', water: 'Analytics', rotations: 'Strategies' }
    document.title = t(titles[route.page]) + ' · Field Shift'
  }, [route.page, t])

  function go(page: Page, options: DestinationOptions = {}) {
    if (page === 'agent') {
      const runField = options.fieldId ?? field?.id
      setAssistantRun(options.jobId && runField ? { id: options.jobId, fieldId: runField,
        season: options.season ?? season, scenario: options.scenario ?? scenario } : undefined)
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
    const nextField = target.page === 'strategies' || target.page === 'analytics' ? selectedField?.id : field?.id
    setAddress({ page: target.page, fieldId: options.fieldId ?? nextField, season: nextSeason,
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
  function chooseWorkspaceField(fieldId: string) {
    if (!fieldId) { setLastField(undefined); rememberSelectedField('') }
    setAddress({ ...route, fieldId, scenario, jobId: undefined, forecastDay: undefined })
  }
  function openMapLayer(layer: 'ndvi_variability' | 'terrain', focus?: MapFocus) {
    go('fields', { layer, mapFocus: focus, season: focus?.season ?? season })
  }
  function openAssistantScreen(screen: AssistantScreen) {
    if (screen === 'water') go('analytics', { section: 'water' })
    else if (screen === 'rotations') go('strategies')
    else go(screen)
  }
  function addCreatedField(created: Field) {
    workspace.update(current => current.fields.some(item => item.id === created.id) ? current : {
      fields: [...current.fields, created], farm: { ...current.farm, total_area_ha: current.farm.total_area_ha + created.area_ha },
    })
  }
  function createField(created: Field) {
    addCreatedField(created)
    if (parseRoute(location.href).page === 'fields') go(fields.length ? 'fields' : 'strategies', { fieldId: created.id })
  }
  function startSetup() {
    if (!canEdit) return
    const id = setupInProgress ?? crypto.randomUUID()
    rememberSetup(id); setSetupInProgress(id); setAssistantOpen(false)
    setAddress({ page: 'fields', setupId: id, fieldId: field?.id, season, scenario })
    window.scrollTo(0, 0)
  }
  function closeSetup(next: Route) { setAddress(next, true); window.scrollTo(0, 0) }
  function finishSetup(created: Field, startSeason: number) {
    if (parseRoute(location.href).setupId !== route.setupId) return
    addCreatedField(created); setSetupInProgress(pendingSetup())
    setLastField(created.id); rememberSelectedField(created.id)
    closeSetup({ page: 'strategies', fieldId: created.id, scenario, season: startSeason })
    setStrategiesVisited(true)
  }
  function goHome() {
    setAssistantOpen(false)
    setAddress({ page: fields.length ? 'strategies' : 'fields', home: true, fieldId: selectedField?.id, season, scenario })
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
    if (route.setupId && canEdit) content = <Suspense fallback={<Loading />}><FieldOnboarding key={route.setupId} id={route.setupId} fields={fields} season={farm.season}
      onCancel={() => closeSetup({ page: 'fields', fieldId: field?.id, season, scenario })} onComplete={finishSetup} /></Suspense>
    else if (missingField && route.page !== 'analytics') content = <Empty><p>{t('The linked field is no longer available.')}</p><button className="button" type="button" onClick={() => go('fields')}>{t('Open Fields')}</button></Empty>
    else if (route.page === 'fields' || route.page === 'agent') content = (
      <FieldsPage key={season + ':' + Boolean(route.addField)} fields={fields} selected={field} season={season} initialDrawing={route.addField}
        initialAnalysis={route.analysis} showNdviLayer={route.layer === 'ndvi_variability'} showTerrainLayer={route.layer === 'terrain'} mapFocus={route.mapFocus}
        chooseField={chooseField} onCreate={createField} onUpdate={updateField} reloadFields={workspace.reload} openForecast={openFieldForecast} onSetup={canEdit ? startSetup : undefined}
        setupLabel={setupInProgress ? (language === 'uk' ? 'Продовжити налаштування поля' : 'Continue field setup') : undefined}
        openStrategies={() => go('strategies', { season: Math.max(season, farm.season + 1) })}
        openAnalytics={(section = 'overview') => go('analytics', { section })} openAgent={() => setAssistantOpen(true)} />
    )
    else if (route.page === 'farm') content = <FarmPage farm={farm} fields={fields} refresh={workspace.reload}
      openVegetation={fieldId => go('analytics', { fieldId, section: 'vegetation' })}
      openStrategies={() => go('strategies')}
      onDefaultsSaved={defaults => workspace.update(current => ({ ...current, farm: { ...current.farm, defaults } }))}
      onObservationSaved={observation => workspace.update(current => ({ ...current,
        farm: { ...current.farm, observations: [observation, ...current.farm.observations.filter(({ id }) => id !== observation.id)] } }))}
      onAddField={startSetup} openField={fieldId => go('fields', { fieldId })} openWater={openFieldForecast} />
    else if (route.page === 'analytics') content = <Suspense fallback={<Loading text={t('Loading workspace…')} />}>
      <AnalyticsPage fields={fields} field={field} context={context} jobId={route.jobId} forecastDay={route.forecastDay}
        chooseField={chooseWorkspaceField} addField={() => go('fields', { addField: true })}
        openAgent={jobId => go('agent', { jobId })} />
    </Suspense>
  }
  const keepStrategies = !route.setupId && farm && !missingField && (strategiesVisited || route.page === 'strategies')
  return <NotificationProvider fields={fields} season={farm?.season ?? new Date().getUTCFullYear()} farmKey={farm?.name ?? 'workspace'}
    openAgent={item => { go('agent', { fieldId: item.fieldId, season: farm?.season, scenario: 'baseline', signal: item.kind === 'water' ? 'water' : 'vegetation' }) }}
    openAnalysis={item => item.kind === 'water'
      ? go('analytics', { fieldId: item.fieldId, season: farm?.season, section: 'water', forecastDay: item.date, jobId: null })
      : go('analytics', { fieldId: item.fieldId, season: farm?.season, section: 'vegetation' })}>
    <Shell route={route} homeAddress={routeAddress({ page: 'strategies', home: true, fieldId: selectedField?.id, season, scenario })} navigateHome={goHome} farmName={farm?.name} navigate={go} assistantOpen={assistantOpen} openAssistant={() => setAssistantOpen(true)}>
      {content}
      {keepStrategies && <div hidden={route.page !== 'strategies'} inert={route.page !== 'strategies'} className="strategies-outlet">
        <Suspense fallback={<Loading text={t('Loading workspace…')} />}><StrategiesPage field={selectedField} fields={fields}
          chooseField={chooseWorkspaceField} openAgent={() => setAssistantOpen(true)} /></Suspense>
      </div>}
    </Shell>
    {farm && !route.setupId && <Assistant field={missingField ? undefined : field} season={season} scenario={scenario} runId={assistantRunId} open={assistantOpen} onOpenChange={setAssistantOpen}
      openScreen={openAssistantScreen} openVegetation={ndvi => ndvi ? openMapLayer('ndvi_variability') : go('analytics', { section: 'vegetation' })}
      openForecast={day => { if (field) openFieldForecast(field.id, day) }}
      openRun={jobId => go('analytics', { section: 'water', jobId })} clearRun={() => setAssistantRun(undefined)} />}
  </NotificationProvider>
}
