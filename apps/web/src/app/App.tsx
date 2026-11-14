import { useLocale } from '../shared/locale'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { scenarioOrDefault, type Field, type Scenario } from '../api/contracts'
import { Assistant } from '../features/assistant/Assistant'
import { FarmPage } from '../features/farm/FarmPage'
import { FieldsPage } from '../features/fields/FieldsPage'
import { RotationsPage } from '../features/rotations/RotationsPage'
import { WaterPage } from '../features/water/WaterPage'
import { Empty, ErrorBox, Loading } from '../shared/ui'
import { useResource } from '../shared/useResource'
import { parseRoute, routeAddress, type Page, type Route } from './routing'
import { Shell } from './Shell'
import { NotificationProvider } from '../features/notifications/Notifications'

const noFields: Field[] = []

function loadWorkspace(signal: AbortSignal) {
  return Promise.all([api.fields(signal), api.farm(signal)]).then(([fields, farm]) => ({ fields, farm }))
}

export function App() {
  const { t } = useLocale()
  const [route, setRoute] = useState(() => parseRoute(location.href))
  const workspace = useResource(loadWorkspace)
  const fields = workspace.data?.fields ?? noFields
  const farm = workspace.data?.farm
  const requestedField = fields.find(({ id }) => id === route.fieldId)
  const missingField = route.page !== 'farm' && Boolean(route.fieldId) && !requestedField
  const field = missingField ? undefined : requestedField ?? fields.find(({ id }) => id === 'F-03') ?? fields[0]
  const scenario = scenarioOrDefault(route.scenario)
  // «Польовий помічник» opens over any page; an /agent address (old links, bookmarks) opens it over Fields.
  const [assistantOpen, setAssistantOpen] = useState(() => route.page === 'agent')
  useEffect(() => {
    if (route.page !== 'agent') return
    const address = routeAddress({ page: 'fields', fieldId: route.fieldId, scenario: route.scenario })
    history.replaceState(null, '', address)
    setRoute(parseRoute(address))
    setAssistantOpen(true)
  }, [route])

  useEffect(() => {
    function onPopState() { setRoute(parseRoute(location.href)) }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    document.title = t(route.page === 'farm' ? 'Farm management' : route.page[0].toUpperCase() + route.page.slice(1)) + ' · Field Shift'
  }, [route.page, t])

  const setAddress = useCallback((next: Route) => {
    const address = routeAddress(next)
    if (address === location.pathname + location.search) return
    history.pushState(null, '', address)
    setRoute(parseRoute(address))
  }, [])

  function go(page: Page, options?: { fieldId?: string; jobId?: string | null; scenario?: Scenario; addField?: boolean; forecastDay?: string; analysis?: boolean; layer?: Route['layer']; signal?: Route['signal'] }) {
    if (page === 'agent') { // the assistant opens over the current page, for the field asked about
      if (options?.fieldId && options.fieldId !== field?.id) setAddress({ page: route.page, fieldId: options.fieldId, scenario })
      setAssistantOpen(true)
      return
    }
    const sameAnalysis = page === 'water'
      && (route.page === 'water' || route.page === 'agent')
      && (!options?.fieldId || options.fieldId === field?.id)
      && (!options?.scenario || options.scenario === scenario)
    setAddress({
      page,
      fieldId: options?.fieldId ?? field?.id,
      scenario: options?.scenario ?? scenario,
      jobId: options?.jobId === null ? undefined : options?.jobId ?? (sameAnalysis ? route.jobId : undefined),
      addField: options?.addField,
      forecastDay: options?.forecastDay,
      analysis: options?.analysis,
      layer: options?.layer,
      signal: options?.signal,
    })
    if (page !== route.page) window.scrollTo(0, 0)
  }

  function chooseField(fieldId: string) {
    setAddress({ page: route.page, fieldId, scenario })
  }

  function openFieldForecast(fieldId: string, forecastDay?: string) {
    go('water', { fieldId, forecastDay, jobId: null })
    window.scrollTo(0, 0)
  }

  function showWaterJob(jobId: string, value?: Scenario) {
    if (routeAddress(route) !== routeAddress(parseRoute(location.href))) return
    go('water', { jobId, scenario: value })
  }

  function chooseScenario(value: Scenario) {
    setAddress({ page: route.page, fieldId: field?.id, scenario: value })
  }

  function createField(fieldId: string) {
    if (parseRoute(location.href).page === 'fields') go('fields', { fieldId })
    workspace.reload()
  }

  function updateField(updated: Field) {
    workspace.update(({ fields, farm }) => {
      const previous = fields.find(({ id }) => id === updated.id)
      if (!previous) return { fields, farm }
      return {
        fields: fields.map((item) => item.id === updated.id ? updated : item),
        farm: { ...farm, total_area_ha: farm.total_area_ha + updated.area_ha - previous.area_ha },
      }
    })
  }

  let content
  if (workspace.pending) content = <Loading text={t('Loading workspace…')} />
  else if (workspace.error) content = <ErrorBox message={workspace.error} retry={workspace.reload} />
  else if (farm) {
    const season = farm.season
    const context = field ? { field_id: field.id, season, scenario } : undefined
    if (missingField) content = <Empty><p>{t('The linked field is no longer available.')}</p>
      <button className="button" type="button" onClick={() => go('fields')}>{t('Open Fields')}</button></Empty>
    else if (route.page === 'fields' || route.page === 'agent') content = (
      <FieldsPage key={season} fields={fields} selected={field} season={season} initialDrawing={route.addField} initialAnalysis={route.analysis} showNdviLayer={route.layer === 'ndvi_variability'}
        chooseField={chooseField} onCreate={createField} onUpdate={updateField} reloadFields={workspace.reload}
        openForecast={openFieldForecast} />
    )
    else if (route.page === 'farm') content = (
      <FarmPage farm={farm} fields={fields} refresh={workspace.reload}
        openVegetation={(fieldId) => go('fields', { fieldId, analysis: true })}
        onDefaultsSaved={(defaults) => workspace.update((current) => ({ ...current, farm: { ...current.farm, defaults } }))}
        onObservationSaved={(observation) => workspace.update((current) => ({
          ...current, farm: { ...current.farm, observations: [observation, ...current.farm.observations.filter(({ id }) => id !== observation.id)] },
        }))}
        onAddField={() => go('fields', { addField: true })} openField={(fieldId) => go('fields', { fieldId })} openWater={openFieldForecast} />
    )
    else if (!context || !field) content = <Empty>
      <p>{t('Add a field to use this workspace.')}</p>
      <button className="button" type="button" onClick={() => go('fields')}>{t('Open Fields')}</button>
    </Empty>
    else if (route.page === 'water') content = (
      <WaterPage key={season} fields={fields} field={field} context={context} jobId={route.jobId} forecastDay={route.forecastDay}
        irrigationAvailable={farm.defaults.irrigation_available}
        openFields={() => { go('fields'); window.scrollTo(0, 0) }}
        chooseField={chooseField} chooseScenario={chooseScenario}
        showJob={showWaterJob} openAgent={(jobId) => go('agent', { jobId })} />
    )
    else if (route.page === 'rotations') content = (
      <RotationsPage key={field.id + ':' + scenario} field={field} fields={fields} context={context}
        chooseField={chooseField} openFields={() => { go('fields'); window.scrollTo(0, 0) }}
        irrigationAvailable={farm.defaults.irrigation_available} chooseScenario={chooseScenario} openWater={() => go('water')} />
    )
  }

  return <NotificationProvider fields={fields} season={farm?.season ?? new Date().getUTCFullYear()} farmKey={farm?.name ?? 'workspace'}
    openAgent={(item) => go('agent', { fieldId: item.fieldId, scenario: 'baseline', jobId: null, signal: item.kind === 'water' ? 'water' : 'vegetation' })}
    openAnalysis={(item) => item.kind === 'water'
      ? openFieldForecast(item.fieldId, item.date)
      : go('fields', { fieldId: item.fieldId, analysis: true, layer: 'ndvi_variability' })}>
    <Shell route={route} farmName={farm?.name} navigate={go} assistantOpen={assistantOpen} openAssistant={() => setAssistantOpen(true)}>{content}</Shell>
    {farm && <Assistant field={missingField ? undefined : field} season={farm.season} scenario={scenario}
      open={assistantOpen} onOpenChange={setAssistantOpen}
      openScreen={(screen) => go(screen)}
      openVegetation={(ndvi) => go('fields', { analysis: true, layer: ndvi ? 'ndvi_variability' : undefined })}
      openForecast={(day) => { if (field) openFieldForecast(field.id, day) }} />}
  </NotificationProvider>
}
