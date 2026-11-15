import assert from 'node:assert/strict'
import test from 'node:test'
import type { ConstraintDecision, ConstraintReplacement } from '../src/api/contracts.ts'
import { decideWithReview } from '../src/features/assistant/constraintDecision.ts'
import { createReplacementReview } from '../src/features/assistant/replacementReview.ts'

// The assistant's replacement flow as Assistant.tsx runs it: decideWithReview sends, and asks through the review
// controller, which the component cancels when the assistant closes, its field context changes, or it unmounts.
// (apps/web has no component renderer; these drive the same code the component calls.)
// "Keep" and every cancellation report `cancelled` (the farmer's choice), not `not_permitted` (a refusal): the widget
// then reopens the choice instead of showing a failure. Changed in Task 05D-1 for that reason.

const KEY = '/agent/assistant/F-03/2026/baseline#0'
const earlier = {
  id: 'pc_irrigation_available', type: 'irrigation_available', value: false, status: 'confirmed', source: 'farmer_ui',
  token: '0b6f4a39-58c4-4d39-9f4d-3c8c25c2f6a1',
}
const replacement = { current: earlier, proposed: { ...earlier, value: true } } as unknown as ConstraintReplacement
const needsConfirmation = Object.assign(new Error('Confirm that this replaces your earlier decision.'), {
  status: 409, body: { code: 'replacement_needs_confirmation', replacements: [replacement] },
})
const decision: ConstraintDecision = { field_id: 'F-03', envelope: { id: 'confirm.c1' } }

/** A fake API that first asks for confirmation, then records what is resent. */
function api(second: () => unknown = () => ({ constraints: [], replaced: [] })) {
  const sent: ConstraintDecision[] = []
  return {
    sent,
    send: async (body: ConstraintDecision) => {
      sent.push(body)
      if (sent.length === 1) throw needsConfirmation
      return second()
    },
  }
}

/** The component's state while a question is open: the assistant open on this field context. */
function assistant() {
  const review = createReplacementReview<ConstraintReplacement>()
  let open = true
  let key = KEY
  let renders = 0
  const unsubscribe = review.subscribe(() => { renders++ })
  review.sync(key, open) // mounted, open on this field context
  return {
    review,
    ask: (items: ConstraintReplacement[]) => review.ask(KEY, items), // asked where the decision was made
    shown: () => review.shown(key, open),
    close() { open = false; review.sync(key, open) }, // Assistant.tsx syncs every change of open...
    reopen() { open = true; review.sync(key, open) },
    moveTo(next: string) { key = next; review.sync(key, open) }, // ...and of the field context
    unmount() { review.cancel(); unsubscribe() }, // the cleanups on unmount
    get renders() { return renders },
  }
}

const tick = () => new Promise((resolve) => setImmediate(resolve))
// A question nobody cancels would leave its promise pending forever: that is a failure here, not a hang.
const it = (name: string, run: () => Promise<void>) => test(name, { timeout: 5_000 }, run)

it('"Replace" resends the same decision naming the exact earlier decision shown', async () => {
  const view = assistant()
  const server = api()
  const outcome = decideWithReview(decision, server.send, view.ask)
  await tick()
  assert.deepEqual(view.shown(), [replacement]) // both decisions are shown before anything is saved
  view.review.answer(true)
  assert.deepEqual(await outcome, { status: 'applied' })
  assert.equal(server.sent.length, 2)
  assert.equal(server.sent[0].replaces, undefined) // the first attempt never claims an approval
  assert.deepEqual(server.sent[1].replaces, [{ type: 'irrigation_available', token: earlier.token }])
  assert.deepEqual(view.shown(), [])
})

it('"Keep" sends nothing more and leaves the earlier decision', async () => {
  const view = assistant()
  const server = api()
  const outcome = decideWithReview(decision, server.send, view.ask)
  await tick()
  view.review.answer(false)
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' })
  assert.equal(server.sent.length, 1)
})

it('closing the assistant cancels the question at once, and reopening does not bring it back', async () => {
  const view = assistant()
  const server = api()
  const outcome = decideWithReview(decision, server.send, view.ask)
  await tick()
  assert.equal(view.shown().length, 1)
  view.close()
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' })
  view.reopen()
  assert.deepEqual(view.shown(), []) // no resurrected dialog
  view.review.answer(true) // a late click on a stale button does nothing
  await tick()
  assert.equal(server.sent.length, 1) // no replacement request after the cancellation
})

it('another field, boundary, season, scenario or run cancels the question', async () => {
  const moves = [
    '/agent/assistant/F-01/2026/baseline#0', KEY.replace('#0', '#1'), '/agent/assistant/F-03/2025/baseline#0',
    '/agent/assistant/F-03/2026/dry_year#0', '/agent/assistant/F-03/2026/baseline/runs/r1#0',
  ]
  for (const next of moves) {
    const view = assistant()
    const server = api()
    const outcome = decideWithReview(decision, server.send, view.ask)
    await tick()
    view.moveTo(next)
    assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' }, next)
    assert.deepEqual(view.shown(), [], next)
    assert.equal(server.sent.length, 1, next)
  }
})

it('unmounting cancels the question and releases the view', async () => {
  const view = assistant()
  const server = api()
  const outcome = decideWithReview(decision, server.send, view.ask)
  await tick()
  view.unmount()
  const after = view.renders
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' })
  view.review.answer(true)
  assert.equal(view.renders, after) // the unmounted view is no longer told about anything
  assert.equal(server.sent.length, 1)
})

it('a decision whose request returns after the assistant closed or moved on is never asked about', async () => {
  for (const leave of ['close', 'move'] as const) {
    const view = assistant()
    const server = api()
    // The farmer leaves while the first request is still on its way.
    const outcome = decideWithReview(decision, async (body) => {
      if (leave === 'close') view.close()
      else view.moveTo('/agent/assistant/F-01/2026/baseline#0')
      return server.send(body)
    }, view.ask)
    assert.deepEqual(await outcome, { status: 'rejected', reason: 'cancelled' }, leave)
    view.reopen()
    assert.deepEqual(view.shown(), [], leave)
    assert.equal(server.sent.length, 1, leave)
  }
})

it('a new question keeps the earlier decision for the older one', async () => {
  const view = assistant()
  const first = api()
  const second = api()
  const older = decideWithReview(decision, first.send, view.ask)
  await tick()
  const newer = decideWithReview(decision, second.send, view.ask)
  await tick()
  assert.deepEqual(await older, { status: 'rejected', reason: 'cancelled' })
  view.review.answer(true)
  assert.deepEqual(await newer, { status: 'applied' })
  assert.equal(first.sent.length, 1)
  assert.equal(second.sent.length, 2)
})

it('an approval that went stale is refused by the server and reported as stale', async () => {
  const view = assistant()
  const server = api(() => {
    throw Object.assign(new Error('This decision changed since you reviewed it. Review the current one again.'), { status: 409, body: {} })
  })
  const outcome = decideWithReview(decision, server.send, view.ask)
  await tick()
  view.review.answer(true)
  assert.deepEqual(await outcome, { status: 'rejected', reason: 'stale_context' })
})

it('other refusals are reported without asking the farmer anything', async () => {
  const view = assistant()
  const boundary = Object.assign(new Error('The field boundary changed. Ask the assistant again.'), { status: 409, body: {} })
  const outcome = await decideWithReview(decision, async () => { throw boundary }, view.ask)
  assert.deepEqual(outcome, { status: 'rejected', reason: 'stale_context' })
  assert.deepEqual(view.shown(), [])
})
