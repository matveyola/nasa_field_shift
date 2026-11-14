// «Польовий помічник» as this app uses it. Vite builds the widget from services/agent/frontend/src/widget
// (vite.config.ts), where its own CI job type-checks and tests it; only the parts used here are typed.
// ponytail: hand-kept types; generate them from the widget (tsc --declaration) if more of its API is used.
import type { JSX, SVGProps } from 'react'

export type ActionType =
  | 'map.set_layers' | 'map.focus_field' | 'time.set_range' | 'chart.focus' | 'compare.set_constraints'
  | 'compare.select_rotations' | 'scenario.set' | 'panel.open' | 'app.open_screen'

export interface AppScreen { screen_id: string; label: string; how_to_open?: string }

export interface HostCapabilities {
  actions: readonly ActionType[]
  simulation: boolean
  screens?: readonly AppScreen[]
}

/** The agent's field context (built by the API, never by the browser). */
export type FieldContext = { field_id: string; revision: number } & Record<string, unknown>

export interface ActionRequest {
  envelope: { field_context_revision: number; action: { type: ActionType } & Record<string, unknown> }
  fieldId: string
  origin: 'agent_auto' | 'farmer_click'
}

export type ActionResult =
  | { status: 'applied'; revision?: number }
  | { status: 'rejected'; reason: 'field_changed' | 'stale_context' | 'module_unavailable' | 'unknown_reference'
      | 'not_permitted' | 'expired' | 'invalid' | 'failed' | 'cancelled' }

export interface FieldShiftAssistantProps {
  transport: { baseUrl: string; headers?: () => Record<string, string> }
  fieldContext: FieldContext | null
  fieldName?: string
  locale: 'uk' | 'en'
  capabilities: HostCapabilities
  onAction: (request: ActionRequest) => ActionResult | Promise<ActionResult>
  trustedSourceHosts?: readonly string[]
  /** The crops this app plans with, as contract crop ids with their names: the only crops an edit may choose. */
  planningCrops?: readonly { id: string; label: string }[]
  open?: boolean
  onOpenChange?: (open: boolean) => void
  launcher?: boolean
  anchor?: Element | null
  placement?: { side?: 'right' | 'left'; offsetX?: number; offsetY?: number; zIndex?: number }
}

export function FieldShiftAssistant(props: FieldShiftAssistantProps): JSX.Element
/** The widget's canonical action check (field, revision, declared capability, known ids); null when allowed. */
export function actionProblem(
  envelope: unknown, ctx: FieldContext | null, caps: HostCapabilities, fieldId: string,
): Extract<ActionResult, { status: 'rejected' }>['reason'] | null
export function FieldMark(props: SVGProps<SVGSVGElement>): JSX.Element
