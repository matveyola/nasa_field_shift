import assert from 'node:assert/strict'
import test from 'node:test'
import type { ConstraintDecision, ConstraintReplacement } from '../src/api/contracts.ts'
import { constraintDecision, decideWithReview, decisionStatus } from '../src/features/assistant/constraintDecision.ts'
import { createReplacementReview } from '../src/features/assistant/replacementReview.ts'

// A farmer's explicit "Reject" in the assistant: the widget sends the host a compare.set_constraints action with
// status `rejected`; the host records it through the same checked flow as a confirmation (Task 05B).

const KEY = '/agent/assistant/F-03/2026/baseline#0'
const proposal = { id: 'k_irrigation', type: 'irrigation_available', value: false, source: 'farmer_text', evidence: 'Поливу немає' }
const inferred = { id: 'k_len', type: 'rotation_length_years', value: 4, source: 'agent_inferred', confidence: 0.6 }

function click(constraints: unknown[], origin: 'farmer_click' | 'agent_auto' = 'farmer_click', fieldId = 'F-03') {
  return {
    envelope: {
      id: 'reject.k_irrigation', field_context_revision: 0, trigger: 'chip', label: 'Reject',
      action: { type: 'compare.set_constraints', constraints },
    },
    fieldId,
    origin,
  }
}

function decided(request: ReturnType<typeof click>): ConstraintDecision {
  const body = constraintDecision(request, 'F-03')
  assert.notEqual(typeof body, 'string', String(body))
  return body as ConstraintDecision
}

const apiError = (status: number, message: string, body: unknown = {}) => Object.assign(new Error(message), { status, body })
const tick = () => new Promise((resolve) => setImmediate(resolve))
const it = (name: string, run: () => Promise<void> | void) => test(name, { timeout: 5_000 }, run)

function openAssistant() {
  const review = createReplacementReview<ConstraintReplacement>()
  review.sync(KEY, true)
  return { review, ask: (items: ConstraintReplacement[]) => review.ask(KEY, items) }
}

it("the farmer's Reject is sent as a rejected decision and saved", async () => {
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const sent: ConstraintDecision[] = []
  const outcome = await decideWithReview(body, async (d) => { sent.push(d) }, async () => true)
  assert.deepEqual(outcome, { status: 'applied' })
  assert.equal(sent.length, 1)
  const action = (sent[0].envelope as { action: { constraints: { status: string }[] } }).action
  assert.deepEqual(action.constraints.map((c) => c.status), ['rejected'])
  assert.equal(sent[0].replaces, undefined)
})

it('a rejected inferred proposal is the farmer’s decision too, and stays marked as inferred', async () => {
  const body = decided(click([{ ...inferred, status: 'rejected' }]))
  const [constraint] = (body.envelope as { action: { constraints: Record<string, unknown>[] } }).action.constraints
  assert.equal(constraint.status, 'rejected')
  assert.equal(constraint.source, 'agent_inferred')
})

it('the agent can never reject on the farmer’s behalf', () => {
  assert.equal(constraintDecision(click([{ ...proposal, status: 'rejected' }], 'agent_auto'), 'F-03'), 'not_permitted')
  assert.equal(constraintDecision(click([{ ...inferred, status: 'rejected' }], 'agent_auto'), 'F-03'), 'not_permitted')
  // Not decided yet: a proposal is never sent, whatever the action says.
  assert.equal(constraintDecision(click([{ ...proposal, status: 'proposed' }]), 'F-03'), 'not_permitted')
})

it('a failed save is reported as a failure, not as a rejection', async () => {
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const outcome = await decideWithReview(body, async () => { throw apiError(503, 'Database is unavailable') }, async () => true)
  assert.deepEqual(outcome, { status: 'rejected', reason: 'failed' })
  const offline = await decideWithReview(body, async () => { throw new TypeError('Failed to fetch') }, async () => true)
  assert.deepEqual(offline, { status: 'rejected', reason: 'failed' })
})

it('a rejection made for an older boundary or another field is stale', async () => {
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const boundary = await decideWithReview(body,
    async () => { throw apiError(409, 'The field boundary changed. Ask the assistant again.') }, async () => true)
  assert.deepEqual(boundary, { status: 'rejected', reason: 'stale_context' })
  assert.equal(constraintDecision(click([{ ...proposal, status: 'rejected' }], 'farmer_click', 'F-01'), 'F-03'), 'field_changed')
})

const confirmedEarlier = { ...proposal, id: 'pc_irrigation_available', status: 'confirmed', token: '0b6f4a39-58c4-4d39-9f4d-3c8c25c2f6a1' }
const replaceConfirmed = apiError(409, 'Confirm that this replaces your earlier decision.', {
  code: 'replacement_needs_confirmation',
  replacements: [{ current: confirmedEarlier, proposed: { ...proposal, status: 'rejected' } }],
})

it('rejecting an earlier confirmed decision asks first; "Keep" leaves it and sends nothing more', async () => {
  const view = openAssistant()
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const sent: ConstraintDecision[] = []
  const outcome = decideWithReview(body, async (d) => { sent.push(d); throw replaceConfirmed }, view.ask)
  await tick()
  assert.equal(view.review.shown(KEY, true).length, 1) // the confirmed decision and the rejection, side by side
  view.review.answer(false)
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' })
  assert.equal(sent.length, 1)
})

it('rejecting an earlier confirmed decision is saved once the farmer approves the replacement', async () => {
  const view = openAssistant()
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const sent: ConstraintDecision[] = []
  const outcome = decideWithReview(body, async (d) => {
    sent.push(d)
    if (sent.length === 1) throw replaceConfirmed
  }, view.ask)
  await tick()
  view.review.answer(true)
  assert.deepEqual(await outcome, { status: 'applied' })
  assert.deepEqual(sent[1].replaces, [{ type: 'irrigation_available', token: confirmedEarlier.token }])
})

it('a replacement approved while another tab changed the decision is refused as stale', async () => {
  const view = openAssistant()
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const outcome = decideWithReview(body, async (d) => {
    if (!d.replaces) throw replaceConfirmed
    throw apiError(409, 'This decision changed since you reviewed it. Review the current one again.')
  }, view.ask)
  await tick()
  view.review.answer(true)
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'stale_context' })
})

it('closing the assistant while asked cancels the rejection: nothing more is sent', async () => {
  const view = openAssistant()
  const body = decided(click([{ ...proposal, status: 'rejected' }]))
  const sent: ConstraintDecision[] = []
  const outcome = decideWithReview(body, async (d) => { sent.push(d); throw replaceConfirmed }, view.ask)
  await tick()
  view.review.sync(KEY, false)
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' })
  view.review.sync(KEY, true)
  assert.deepEqual(view.review.shown(KEY, true), [])
  assert.equal(sent.length, 1)
})

it('the planning panel never shows a rejected condition like a confirmed one', () => {
  assert.deepEqual(decisionStatus({ status: 'confirmed' }), { label: 'Confirmed by you', tone: 'confirmed' })
  assert.deepEqual(decisionStatus({ status: 'rejected' }), { label: 'Rejected by you', tone: 'rejected' })
})
