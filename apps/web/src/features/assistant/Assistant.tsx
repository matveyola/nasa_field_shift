import '@fontsource-variable/onest' // the widget's composer font
import { useEffect, useMemo, useReducer, useState } from 'react'
import './assistant.css'
import {
  FieldShiftAssistant, actionProblem, type ActionRequest, type ActionResult, type FieldContext, type HostCapabilities,
} from '@field-shift/agent-widget'
import { api, apiBase, applicationHeaders, request } from '../../api/client'
import type { ConstraintReplacement, Field } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { HOST_ACTIONS, hostStep, type AssistantScreen } from './assistantActions'
import { assistantPath, conversationKey, historicalPeriod } from './assistantRun'
import { CONSTRAINTS_CHANGED, CONSTRAINT_ACTION, constraintDecision, constraintSummary, decideWithReview } from './constraintDecision'
import { createReplacementReview } from './replacementReview'
import { EDITABLE_CROP_IDS, editableCropOptions } from './constraintCrops'
import { planningUkrainian } from '../rotations/planningUkrainian'

export type { AssistantScreen } from './assistantActions'

type Props = {
  field: Field | undefined
  season: number
  scenario: string
  /** A saved historical run the farmer explicitly selected; the API checks it and builds the context from it. */
  runId?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  openScreen: (screen: AssistantScreen) => void
  openVegetation: (ndviLayer: boolean) => void
  openForecast: (day?: string) => void
  openRun: (runId: string) => void
  /** Leave the selected run and talk about the field's current data instead (the farmer's choice). */
  clearRun: () => void
}

const applied: ActionResult = { status: 'applied' }

/** «Польовий помічник» for the selected field. The API builds its field context and signs every turn. */
export function Assistant({ field, season, scenario, runId, open, onOpenChange, openScreen, openVegetation, openForecast, openRun, clearRun }: Props) {
  const { language, t, shortDate } = useLocale()
  const path = field ? assistantPath(field.id, season, scenario, runId) : undefined
  // The context belongs to the address it was loaded for: after a field, scenario or run change the
  // previous one is never shown or used for actions while the new one loads.
  const contextKey = path && `${path}#${field?.boundary_revision}`
  const [loaded, setLoaded] = useState<{ key: string; context: FieldContext | null; error?: string }>()
  // Bumped after the farmer's decision is recorded: the context the API builds then carries it.
  const [reloads, setReloads] = useState(0)
  // A decision that would change an earlier one waits for the farmer's explicit choice; nothing is saved until
  // then. Closing the assistant, another field, boundary, season, scenario or run, or unmounting answers "keep",
  // and an answered question never comes back (replacementReview.ts).
  const review = useMemo(() => createReplacementReview<ConstraintReplacement>(), [])
  const [, rerender] = useReducer((count: number) => count + 1, 0)
  useEffect(() => review.subscribe(rerender), [review])
  useEffect(() => review.sync(contextKey, open), [contextKey, open, review]) // closing or moving on cancels
  useEffect(() => () => review.cancel(), [review]) // unmounting cancels
  const reviewing = review.shown(contextKey, open)
  const current = loaded?.key === contextKey ? loaded : undefined
  const context = current?.context ?? null
  const period = runId ? historicalPeriod(context) : undefined
  // The navigation's assistant button (the disc in the middle of the phone's tab bar): the panel unfolds from it.
  const [anchor, setAnchor] = useState<Element | null>(null)
  useEffect(() => setAnchor(document.querySelector('.nav-assistant-mark')), [])

  // Fresh saved evidence each time the assistant opens, and whenever the field, season or scenario changes.
  useEffect(() => {
    if (!path || !open) return
    const controller = new AbortController()
    request<FieldContext>(`${path}/field-context?locale=${language}`, { signal: controller.signal })
      .then((value) => setLoaded({ key: contextKey!, context: value }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        // A selected run that is missing, unfinished or stale is reported, never swapped for other data.
        setLoaded({ key: contextKey!, context: null, error: runId && error instanceof Error ? error.message : undefined })
      })
    return () => controller.abort()
  }, [path, contextKey, runId, open, language, reloads])

  // The crops a farmer may choose when editing a proposed condition: the planning crops whose identity the agent
  // shares (constraintCrops.ts). Other crops in a proposal make it not editable, never rewritten.
  const planningCrops = useMemo(
    () => editableCropOptions((crop) => language === 'uk' ? planningUkrainian[crop] ?? crop : crop),
    [language],
  )

  const capabilities: HostCapabilities = useMemo(() => ({
    // The farmer's confirmation of proposed constraints is recorded by this host (constraintDecision.ts). It is
    // offered to the widget only: the API still signs HOST_ACTIONS alone as the agent-visible capabilities.
    actions: [...HOST_ACTIONS, CONSTRAINT_ACTION],
    simulation: false,
    screens: [
      { screen_id: 'fields', label: t('Fields') },
      { screen_id: 'water', label: t('Water') },
      { screen_id: 'rotations', label: t('Rotations') },
      { screen_id: 'farm', label: t('Farm management') },
    ],
  }), [t])

  function onAction(action: ActionRequest): ActionResult | Promise<ActionResult> {
    const { envelope, fieldId } = action
    // The widget's canonical check, again at the host: the field the action was made for, its revision,
    // a capability this host declared, and layer / chart / metric / screen ids that exist in this context.
    const problem = actionProblem(envelope, context, capabilities, fieldId)
    if (problem) return { status: 'rejected', reason: problem }
    if (envelope.action.type === CONSTRAINT_ACTION) return decide(action)
    const step = hostStep(envelope.action)
    if (typeof step === 'string') return { status: 'rejected', reason: step }
    if (step.kind === 'run') {
      if (!runId) return { status: 'rejected', reason: 'unknown_reference' }
      openRun(runId)
    } else if (step.kind === 'vegetation') openVegetation(step.ndviLayer)
    else if (step.kind === 'forecast') openForecast(step.day)
    else openScreen(step.screen)
    // On a phone the assistant covers the screen: close it so the farmer sees what it opened.
    if (matchMedia('(max-width: 640px)').matches) onOpenChange(false)
    return applied
  }

  /** Records the farmer's decision; the API re-checks the field, its boundary revision and every constraint. */
  async function decide(action: ActionRequest): Promise<ActionResult> {
    const body = constraintDecision(action, field?.id, EDITABLE_CROP_IDS)
    if (typeof body === 'string') return { status: 'rejected', reason: body }
    const asked = contextKey
    const outcome = await decideWithReview(
      body,
      (decision) => api.decideConstraints(decision.field_id, season, decision),
      // Asked only where the decision was made, while the assistant is still open there; otherwise "keep".
      (items) => asked ? review.ask(asked, items) : Promise.resolve(false),
    )
    if (outcome.status === 'rejected') return outcome
    setReloads((value) => value + 1) // the widget shows "confirmed" once the host's context holds it
    window.dispatchEvent(new Event(CONSTRAINTS_CHANGED))
    return applied
  }

  // The field line says when the assistant is explaining a saved historical run rather than current data.
  const fieldName = field && period ? `${field.name} · ${t('Historical run')} ${shortDate(period.start)}–${shortDate(period.end)}` : field?.name

  return (<>
    <FieldShiftAssistant
      key={conversationKey({ fieldId: field?.id, boundaryRevision: field?.boundary_revision, season, scenario, runId })}
      transport={{ baseUrl: path ? apiBase + path : apiBase + '/agent/assistant/none', headers: applicationHeaders }}
      fieldContext={path ? context : null}
      fieldName={fieldName}
      locale={language}
      capabilities={capabilities}
      planningCrops={planningCrops}
      onAction={onAction}
      open={open}
      onOpenChange={onOpenChange}
      launcher={false}
      anchor={anchor}
      // Wide screens: next to the sidebar, at the bottom, nearest the sidebar's assistant item.
      placement={{ side: 'left', offsetX: 248, offsetY: 16 }}
    />
    {reviewing.length > 0 && (
      <div className="assistant-notice" role="alertdialog" aria-labelledby="replace-decision-heading" aria-describedby="replace-decision-list">
        <strong id="replace-decision-heading">{t('Replace your earlier decision?')}</strong>
        <ul id="replace-decision-list">
          {reviewing.map(({ current, proposed }) => (
            <li key={current.type}>
              {t('Now')}: {constraintSummary(current, t)}{current.status === 'rejected' ? ' (' + t('rejected') + ')' : ''}
              {' → '}{t('New')}: {constraintSummary({ ...proposed, token: '' }, t)}{proposed.status === 'rejected' ? ' (' + t('rejected') + ')' : ''}
            </li>
          ))}
        </ul>
        <div className="assistant-notice-actions">
          <button className="button subtle" type="button" onClick={() => review.answer(false)}>{t('Keep the earlier decision')}</button>
          <button className="button primary" type="button" onClick={() => review.answer(true)}>{t('Replace')}</button>
        </div>
      </div>
    )}
    {open && current?.error && (
      <div className="assistant-run-error" role="alert">
        <span>{t(current.error)}</span>
        <button className="button subtle" type="button" onClick={clearRun}>{t('Ask about current field data')}</button>
      </div>
    )}
  </>)
}
