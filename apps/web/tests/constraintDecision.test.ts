import assert from 'node:assert/strict'
import test from 'node:test'
import type { PlanningConstraint } from '../src/api/contracts.ts'
import { HOST_ACTIONS, hostStep } from '../src/features/assistant/assistantActions.ts'
import {
  CONSTRAINT_ACTION, constraintDecision, constraintOrigin, constraintSummary, refusalReason, replacementsToConfirm, withReplacements,
} from '../src/features/assistant/constraintDecision.ts'

const noIrrigation = { id: 'c1', type: 'irrigation_available', value: false, status: 'confirmed', source: 'farmer_text', evidence: "I don't have irrigation" }
const noSunflower = { id: 'c2', type: 'excluded_crops', value: ['sunflower'], status: 'confirmed', source: 'farmer_text', evidence: 'exclude sunflower' }

function request(constraints: unknown, update: Partial<{ fieldId: string; origin: 'agent_auto' | 'farmer_click'; type: string }> = {}) {
  return {
    envelope: { id: 'confirm.c1', field_context_revision: 0, trigger: 'chip', label: 'Confirm', action: { type: update.type ?? CONSTRAINT_ACTION, constraints } },
    fieldId: update.fieldId ?? 'F-03',
    origin: update.origin ?? 'farmer_click',
  }
}

test("the farmer's click on decided constraints is sent for the field shown, unchanged", () => {
  const confirmed = request([noIrrigation, noSunflower])
  assert.deepEqual(constraintDecision(confirmed, 'F-03'), { field_id: 'F-03', envelope: confirmed.envelope })
  const rejected = request([{ ...noIrrigation, status: 'rejected' }])
  assert.deepEqual(constraintDecision(rejected, 'F-03'), { field_id: 'F-03', envelope: rejected.envelope })
})

test('a proposal is never sent: not undecided, not inferred, not applied by the agent itself', () => {
  assert.equal(constraintDecision(request([{ ...noIrrigation, status: 'proposed' }]), 'F-03'), 'not_permitted')
  assert.equal(constraintDecision(request([noIrrigation, { ...noSunflower, status: 'proposed' }]), 'F-03'), 'not_permitted')
  const inferred = { id: 'c3', type: 'rotation_length_years', value: 4, status: 'proposed', source: 'agent_inferred', confidence: 0.6 }
  assert.equal(constraintDecision(request([inferred]), 'F-03'), 'not_permitted') // an inference needs approval too
  assert.notEqual(typeof constraintDecision(request([{ ...inferred, status: 'confirmed' }]), 'F-03'), 'string')
  // An action the agent marks "auto" (or claims is confirmed) is not the farmer's click.
  assert.equal(constraintDecision(request([noIrrigation], { origin: 'agent_auto' }), 'F-03'), 'not_permitted')
})

test('a decision made for another field, or with no field shown, is stale', () => {
  assert.equal(constraintDecision(request([noIrrigation], { fieldId: 'F-01' }), 'F-03'), 'field_changed')
  assert.equal(constraintDecision(request([noIrrigation]), undefined), 'field_changed')
})

test('malformed constraint actions are refused before anything is sent', () => {
  assert.equal(constraintDecision(request([noIrrigation], { type: 'map.set_layers' }), 'F-03'), 'invalid')
  assert.equal(constraintDecision(request([]), 'F-03'), 'invalid')
  assert.equal(constraintDecision(request('irrigation'), 'F-03'), 'invalid')
  assert.equal(constraintDecision(request(undefined), 'F-03'), 'invalid')
  assert.equal(constraintDecision(request([null]), 'F-03'), 'not_permitted')
  assert.equal(constraintDecision(request([{ type: 'irrigation_available', value: false }]), 'F-03'), 'not_permitted') // no status
})

test("the API's refusals reach the widget as its reasons", () => {
  assert.equal(refusalReason(409, 'This decision was made for another field.'), 'field_changed')
  assert.equal(refusalReason(404, 'Field F-03 was not found'), 'field_changed')
  assert.equal(refusalReason(409, 'The field boundary changed. Ask the assistant again.'), 'stale_context')
  assert.equal(refusalReason(409, 'Only constraints the farmer confirmed or rejected can be saved.'), 'invalid')
  assert.equal(refusalReason(422, 'Input should be a valid boolean'), 'invalid')
  // Approved against an earlier decision that has changed since: stale, nothing overwritten.
  assert.equal(refusalReason(409, 'This decision changed since you reviewed it. Review the current one again.'), 'stale_context')
  assert.equal(refusalReason(503, 'Database is unavailable'), 'failed')
  assert.equal(refusalReason(undefined, 'Failed to fetch'), 'failed')
})

test('the agent-visible host capabilities stay unchanged; confirmation is handled before the host steps', () => {
  // The API signs HOST_ACTIONS (kept in step with signing.py by assistantActions.test.ts); the widget alone is
  // told this host records the farmer's confirmations.
  assert.equal((HOST_ACTIONS as readonly string[]).includes(CONSTRAINT_ACTION), false)
  assert.equal(hostStep({ type: CONSTRAINT_ACTION, constraints: [noIrrigation] }), 'module_unavailable')
})

const t = (text: string) => text

test('a recorded decision reads as the farmer said it, and says where it came from', () => {
  const recorded = (c: object) => ({ id: 'pc', status: 'confirmed', source: 'farmer_ui', ...c }) as PlanningConstraint
  assert.equal(constraintSummary(recorded({ type: 'irrigation_available', value: false }), t), 'No irrigation')
  assert.equal(constraintSummary(recorded({ type: 'irrigation_available', value: true }), t), 'Irrigation is available')
  assert.equal(constraintSummary(recorded({ type: 'excluded_crops', value: ['sunflower', 'winter_wheat'] }), t), 'Exclude: sunflower, winter wheat')
  assert.equal(constraintSummary(recorded({ type: 'rotation_length_years', value: 4 }), t), 'Rotation length: 4 years')
  assert.equal(constraintSummary(recorded({ type: 'fertilizer_budget', value: 'minimize_n' }), t), 'Fertilizer budget: minimize nitrogen')
  assert.deepEqual(constraintOrigin(recorded({ type: 'organic_only', value: true, source: 'agent_inferred', confidence: 0.5 })), { kind: 'inferred' })
  assert.deepEqual(constraintOrigin(recorded({ type: 'organic_only', value: true, source: 'farmer_text', evidence: 'only organic' })),
    { kind: 'farmer_words', evidence: 'only organic' })
  assert.deepEqual(constraintOrigin(recorded({ type: 'organic_only', value: true })), { kind: 'farmer' })
})

const earlier = { id: 'pc_excluded_crops', type: 'excluded_crops', value: ['sunflower'], status: 'confirmed', source: 'farmer_text', evidence: 'exclude sunflower', token: '6f1c2b8e-3f43-4f7e-9a51-0b4d5c6e7f80' }
const asked = {
  detail: 'Confirm that this replaces your earlier decision.',
  code: 'replacement_needs_confirmation',
  replacements: [{ current: earlier, proposed: { ...noSunflower, value: ['maize'] } }],
}

test('a change to an earlier decision is shown for confirmation, never applied by a plain click', () => {
  const replacements = replacementsToConfirm(409, asked)
  assert.deepEqual(replacements, asked.replacements)
  // Any other refusal is not a question for the farmer: the action is simply refused.
  assert.equal(replacementsToConfirm(409, { detail: 'The field boundary changed. Ask the assistant again.' }), undefined)
  assert.equal(replacementsToConfirm(422, asked), undefined)
  assert.equal(replacementsToConfirm(409, { ...asked, replacements: [] }), undefined)
  assert.equal(replacementsToConfirm(409, { ...asked, replacements: [{ current: { type: 'excluded_crops' }, proposed: {} }] }), undefined) // no token
  assert.equal(replacementsToConfirm(409, null), undefined)
})

test("the farmer's confirmation names exactly the earlier decisions shown, by their tokens", () => {
  const decision = constraintDecision(request([{ ...noSunflower, value: ['maize'] }]), 'F-03')
  assert.notEqual(typeof decision, 'string')
  if (typeof decision === 'string') return
  const confirmed = withReplacements(decision, replacementsToConfirm(409, asked)!)
  assert.deepEqual(confirmed.replaces, [{ type: 'excluded_crops', token: '6f1c2b8e-3f43-4f7e-9a51-0b4d5c6e7f80' }])
  assert.equal(confirmed.envelope, decision.envelope) // the same decision, nothing else changed
  assert.equal(decision.replaces, undefined) // the first attempt never claims an acknowledgement
})

