import '@fontsource-variable/onest' // the widget's composer font
import { useEffect, useMemo, useState } from 'react'
import {
  FieldShiftAssistant, type ActionRequest, type ActionResult, type FieldContext, type HostCapabilities,
} from '@field-shift/agent-widget'
import { apiBase, applicationHeaders, request } from '../../api/client'
import type { Field } from '../../api/contracts'
import { useLocale } from '../../shared/locale'

export type AssistantScreen = 'fields' | 'water' | 'rotations' | 'farm'

type Props = {
  field: Field | undefined
  season: number
  scenario: string
  open: boolean
  onOpenChange: (open: boolean) => void
  openScreen: (screen: AssistantScreen) => void
  openVegetation: (ndviLayer: boolean) => void
  openForecast: (day?: string) => void
}

const applied: ActionResult = { status: 'applied' }

/** «Польовий помічник» for the selected field. The API builds its field context and signs every turn. */
export function Assistant({ field, season, scenario, open, onOpenChange, openScreen, openVegetation, openForecast }: Props) {
  const { language, t } = useLocale()
  const path = field ? `/agent/assistant/${encodeURIComponent(field.id)}/${season}/${scenario}` : undefined
  const [context, setContext] = useState<FieldContext | null>(null)
  // The navigation's assistant button (the disc in the middle of the phone's tab bar): the panel unfolds from it.
  const [anchor, setAnchor] = useState<Element | null>(null)
  useEffect(() => setAnchor(document.querySelector('.nav-assistant-mark')), [])

  // Fresh saved evidence each time the assistant opens, and whenever the field, season or scenario changes.
  useEffect(() => {
    if (!path || !open) return
    const controller = new AbortController()
    request<FieldContext>(`${path}/field-context?locale=${language}`, { signal: controller.signal })
      .then(setContext)
      .catch(() => { if (!controller.signal.aborted) setContext(null) })
    return () => controller.abort()
  }, [path, open, language, field?.boundary_revision])

  const capabilities: HostCapabilities = useMemo(() => ({
    actions: ['map.set_layers', 'map.focus_field', 'chart.focus', 'app.open_screen'],
    simulation: false,
    screens: [
      { screen_id: 'fields', label: t('Fields') },
      { screen_id: 'water', label: t('Water') },
      { screen_id: 'rotations', label: t('Rotations') },
      { screen_id: 'farm', label: t('Farm management') },
    ],
  }), [t])

  function onAction({ envelope }: ActionRequest): ActionResult {
    if (!context || envelope.field_context_revision !== context.revision) return { status: 'rejected', reason: 'stale_context' }
    const action = envelope.action
    const done = () => {
      // On a phone the assistant covers the screen: close it so the farmer sees what it opened.
      if (matchMedia('(max-width: 640px)').matches) onOpenChange(false)
      return applied
    }
    switch (action.type) {
      case 'map.set_layers':
        if (!Array.isArray(action.layer_ids) || !action.layer_ids.includes('l_ndvi_variability')) break
        openVegetation(true)
        return done()
      case 'map.focus_field':
        openScreen('fields')
        return done()
      case 'chart.focus': {
        const highlight = action.highlight as { start?: unknown } | undefined
        if (action.chart_id === 'c_vegetation') openVegetation(false)
        else if (action.chart_id === 'c_water_forecast') openForecast(typeof highlight?.start === 'string' ? highlight.start : undefined)
        else if (action.chart_id === 'c_water_season') openScreen('water')
        else return { status: 'rejected', reason: 'unknown_reference' }
        return done()
      }
      case 'app.open_screen':
        if (!['fields', 'water', 'rotations', 'farm'].includes(action.screen_id as string)) break
        openScreen(action.screen_id as AssistantScreen)
        return done()
    }
    return { status: 'rejected', reason: 'module_unavailable' }
  }

  return (
    <FieldShiftAssistant
      transport={{ baseUrl: path ? apiBase + path : apiBase + '/agent/assistant/none', headers: applicationHeaders }}
      fieldContext={path ? context : null}
      fieldName={field?.name}
      locale={language}
      capabilities={capabilities}
      onAction={onAction}
      open={open}
      onOpenChange={onOpenChange}
      launcher={false}
      anchor={anchor}
      // Wide screens: next to the sidebar, at the bottom, nearest the sidebar's assistant item.
      placement={{ side: 'left', offsetX: 248, offsetY: 16 }}
    />
  )
}
