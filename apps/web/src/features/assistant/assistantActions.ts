// What the app does with an assistant action, kept apart from React so it can be tested on its own.
// The host checks every action twice: the widget's canonical actionProblem (field, revision, declared capability,
// known layer / chart / metric / screen ids), then hostStep below, which only knows this app's few destinations.

export type AssistantScreen = 'fields' | 'water' | 'rotations' | 'farm'

// The actions and screens this host executes. The API signs only these as the client's capabilities
// (apps/api/src/field_shift/modules/agent/signing.py HOST_ACTIONS, HOST_SCREENS): keep the lists in step.
export const HOST_ACTIONS = ['map.set_layers', 'map.focus_field', 'chart.focus', 'app.open_screen'] as const
export const HOST_SCREENS: readonly AssistantScreen[] = ['fields', 'water', 'rotations', 'farm']

export type HostStep =
  | { kind: 'vegetation'; ndviLayer: boolean }
  | { kind: 'forecast'; day?: string }
  | { kind: 'run' } // the saved historical run the assistant is explaining, opened on Water
  | { kind: 'screen'; screen: AssistantScreen }

export type HostRefusal = 'unknown_reference' | 'module_unavailable'

/** Where an action takes the farmer in this app, or why the app cannot apply it. */
export function hostStep(action: { type: string } & Record<string, unknown>): HostStep | HostRefusal {
  switch (action.type) {
    case 'map.set_layers':
      // The only map layer this app can open for the assistant: within-field NDVI variability.
      return Array.isArray(action.layer_ids) && action.layer_ids.length === 1 && action.layer_ids[0] === 'l_ndvi_variability'
        ? { kind: 'vegetation', ndviLayer: true } : 'unknown_reference'
    case 'map.focus_field':
      return { kind: 'screen', screen: 'fields' } // the current field: the action carries no other field
    case 'chart.focus': {
      const highlight = action.highlight as { start?: unknown } | undefined
      if (action.chart_id === 'c_vegetation') return { kind: 'vegetation', ndviLayer: false }
      if (action.chart_id === 'c_water_forecast') return { kind: 'forecast', day: typeof highlight?.start === 'string' ? highlight.start : undefined }
      if (action.chart_id === 'c_water_season') return { kind: 'run' }
      return 'unknown_reference'
    }
    case 'app.open_screen':
      return HOST_SCREENS.includes(action.screen_id as AssistantScreen)
        ? { kind: 'screen', screen: action.screen_id as AssistantScreen } : 'unknown_reference'
  }
  return 'module_unavailable' // constraints, rotations, scenario, panels, time range: not executed by this host
}
