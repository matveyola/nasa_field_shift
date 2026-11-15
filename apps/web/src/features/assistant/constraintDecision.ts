// The farmer's decisions about planning constraints the assistant proposes, kept apart from React so they can
// be tested on their own. The widget shows proposals; only the farmer's click sends one here, and the API checks
// it again (field, boundary revision, contract types) before it records anything. A recorded constraint is a
// planning preference for one field and season: not a measurement, not a farm default, and not (yet) used by
// rotation comparisons or simulations.
import type { ConstraintDecision, ConstraintReplacement, PlanningConstraint } from '../../api/contracts'

export const CONSTRAINT_ACTION = 'compare.set_constraints'

const DECIDED = new Set(['confirmed', 'rejected'])

type Request = {
  envelope: { action: { type: string } & Record<string, unknown> }
  fieldId: string
  origin: 'agent_auto' | 'farmer_click'
}

/**
 * What the host sends the API for a widget action, or why it refuses it. Only a farmer's click with decided
 * (confirmed or rejected) constraints for the field shown now is sent; a proposal the farmer has not decided
 * on, or anything the agent tries to apply by itself, never is.
 */
export function constraintDecision(request: Request, fieldId: string | undefined, crops: ReadonlySet<string> = new Set()):
  ConstraintDecision | 'not_permitted' | 'field_changed' | 'invalid' {
  const { action } = request.envelope
  if (action.type !== CONSTRAINT_ACTION || !Array.isArray(action.constraints) || action.constraints.length === 0) return 'invalid'
  if (request.origin !== 'farmer_click') return 'not_permitted'
  if (!fieldId || request.fieldId !== fieldId) return 'field_changed'
  const decided = action.constraints.every((c: unknown) =>
    typeof c === 'object' && c !== null && DECIDED.has((c as { status?: unknown }).status as string))
  if (!decided) return 'not_permitted'
  // The farmer's edits are checked again here, so an invalid edited value never reaches the API.
  if (action.constraints.some((c: unknown) => editProblem(c as Record<string, unknown>, crops))) return 'invalid'
  return { field_id: request.fieldId, envelope: request.envelope }
}

const CROP_LISTS = new Set(['excluded_crops', 'preferred_crops', 'market_preference'])
const BOOLEANS = new Set(['irrigation_available', 'erosion_protection', 'organic_only'])

/**
 * Why a farmer-edited condition (source `farmer_ui`) cannot be sent, or null. An edit is the farmer's own choice in the
 * app: it carries no quoted words or model confidence, its crops are ones this app and the agent name the same way
 * (constraintCrops.ts), and its value is within the contract's limits. Proposals sent as the assistant made them
 * are left to the API's checks.
 */
export function editProblem(c: Record<string, unknown>, crops: ReadonlySet<string>): 'invalid' | null {
  if (c.source !== 'farmer_ui') return null
  if (c.evidence !== undefined || c.confidence !== undefined) return 'invalid' // no words the farmer did not say
  const value = c.value
  const ok = BOOLEANS.has(c.type as string) ? typeof value === 'boolean'
    : c.type === 'fertilizer_budget' ? value === 'minimize_n' || value === 'low' || value === 'normal'
    : c.type === 'rotation_length_years' ? Number.isInteger(value) && (value as number) >= 2 && (value as number) <= 6
    : CROP_LISTS.has(c.type as string) ? Array.isArray(value) && value.length > 0 && value.every((id) => crops.has(id as string))
    : c.type === 'previous_crop' ? crops.has(value as string)
    : c.type === 'machinery_limit' ? typeof value === 'string' && value.trim() === value && value.length >= 1 && value.length <= 300
    : false
  return ok ? null : 'invalid'
}

/** The widget's reason for an API refusal of a decision. */
export function refusalReason(status: number | undefined, message: string): 'field_changed' | 'stale_context' | 'invalid' | 'failed' {
  if (status === 404 || (status === 409 && /another field/.test(message))) return 'field_changed'
  if (status === 409 && /boundary|changed since/.test(message)) return 'stale_context'
  if (status === 409 || status === 422) return 'invalid'
  return 'failed'
}

type Translate = (text: string) => string

/** A crop id from the contract ("winter_wheat") as words for the farmer. */
export function cropName(id: string, t: Translate): string {
  return t(id.replaceAll('_', ' '))
}

/** One recorded decision in the farmer's words. */
export function constraintSummary(c: PlanningConstraint, t: Translate): string {
  const crops = (value: unknown) => Array.isArray(value) ? value.map((id) => cropName(String(id), t)).join(', ') : cropName(String(value), t)
  const yes = (value: unknown, on: string, off: string) => t(value === true ? on : off)
  switch (c.type) {
    case 'irrigation_available': return yes(c.value, 'Irrigation is available', 'No irrigation')
    case 'erosion_protection': return yes(c.value, 'Protect against erosion', 'No erosion protection needed')
    case 'organic_only': return yes(c.value, 'Organic farming only', 'Not limited to organic farming')
    case 'fertilizer_budget': return t('Fertilizer budget') + ': ' + t(c.value === 'minimize_n' ? 'minimize nitrogen' : String(c.value))
    case 'rotation_length_years': return t('Rotation length') + ': ' + String(c.value) + ' ' + t('years')
    case 'excluded_crops': return t('Exclude') + ': ' + crops(c.value)
    case 'preferred_crops': return t('Prefer') + ': ' + crops(c.value)
    case 'market_preference': return t('Market preference') + ': ' + crops(c.value)
    case 'previous_crop': return t('Previous crop') + ': ' + crops(c.value)
    case 'machinery_limit': return t('Machinery limit') + ': ' + String(c.value)
  }
}

/** How a recorded decision is labelled for the farmer: confirmed and rejected decisions never look alike. */
export function decisionStatus(c: Pick<PlanningConstraint, 'status'>): { label: string; tone: 'confirmed' | 'rejected' } {
  return c.status === 'confirmed'
    ? { label: 'Confirmed by you', tone: 'confirmed' }
    : { label: 'Rejected by you', tone: 'rejected' }
}

/** Where the condition came from: the farmer's own words, or the assistant's inference the farmer approved. */
export function constraintOrigin(c: PlanningConstraint): { kind: 'farmer_words'; evidence: string } | { kind: 'inferred' } | { kind: 'farmer' } {
  if (c.source === 'agent_inferred') return { kind: 'inferred' }
  if ((c.source === 'farmer_text' || c.source === 'farmer_voice') && c.evidence) return { kind: 'farmer_words', evidence: c.evidence }
  return { kind: 'farmer' }
}

/** Told to every open view when the farmer's decisions change, so the Rotations list stays current. */
export const CONSTRAINTS_CHANGED = 'field-shift-constraints-changed'

/**
 * The earlier decisions a refused decision would change, when the API asks the farmer to confirm the replacement
 * (409 `replacement_needs_confirmation`; nothing was saved). Undefined for any other refusal.
 */
export function replacementsToConfirm(status: number | undefined, body: unknown): ConstraintReplacement[] | undefined {
  if (status !== 409 || typeof body !== 'object' || body === null) return undefined
  const { code, replacements } = body as { code?: unknown; replacements?: unknown }
  if (code !== 'replacement_needs_confirmation' || !Array.isArray(replacements) || !replacements.length) return undefined
  const valid = replacements.every((r: unknown) => {
    const current = (r as { current?: Partial<PlanningConstraint> } | null)?.current
    return typeof current?.type === 'string' && typeof current.token === 'string' && current.token !== ''
      && typeof (r as { proposed?: unknown }).proposed === 'object'
  })
  return valid ? replacements as ConstraintReplacement[] : undefined
}

/** The same decision, now naming each exact earlier decision the farmer chose to replace. */
export function withReplacements(decision: ConstraintDecision, replacements: readonly ConstraintReplacement[]): ConstraintDecision {
  return { ...decision, replaces: replacements.map(({ current }) => ({ type: current.type, token: current.token })) }
}

type Outcome = { status: 'applied' } | { status: 'rejected'; reason: 'field_changed' | 'stale_context' | 'invalid' | 'failed' | 'cancelled' }

/**
 * The host's whole flow for one farmer decision. `send` posts it (rejecting with the API's error: status, message
 * and body). If it would change an earlier decision, nothing has been saved: `ask` shows the farmer both and waits.
 * Only "Replace" resends it, naming the exact decisions shown; "Keep" or any cancellation sends nothing more.
 */
export async function decideWithReview(
  decision: ConstraintDecision,
  send: (decision: ConstraintDecision) => Promise<unknown>,
  ask: (replacements: ConstraintReplacement[]) => Promise<boolean>,
): Promise<Outcome> {
  const refused = (error: unknown): Outcome => {
    const { status, message } = (error ?? {}) as { status?: number; message?: unknown }
    return { status: 'rejected', reason: refusalReason(status, typeof message === 'string' ? message : '') }
  }
  try {
    await send(decision)
    return { status: 'applied' }
  } catch (error) {
    const { status, body } = (error ?? {}) as { status?: number; body?: unknown }
    const replacements = replacementsToConfirm(status, body)
    if (!replacements) return refused(error)
    // "Keep" (or any cancellation): the farmer's own choice, not a failure; the earlier decision stands.
    if (!await ask(replacements)) return { status: 'rejected', reason: 'cancelled' }
    try {
      await send(withReplacements(decision, replacements))
      return { status: 'applied' }
    } catch (retry) {
      return refused(retry) // e.g. the decision changed again meanwhile: nothing is overwritten
    }
  }
}
