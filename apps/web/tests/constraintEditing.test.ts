import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import type { ConstraintDecision, ConstraintReplacement } from '../src/api/contracts.ts'
import { EDITABLE_CROP_IDS, SHARED_CROP_IDS, UNSHARED_PLANNING_CROPS, editableCropOptions } from '../src/features/assistant/constraintCrops.ts'
import { constraintDecision, decideWithReview, editProblem } from '../src/features/assistant/constraintDecision.ts'
import { createReplacementReview } from '../src/features/assistant/replacementReview.ts'
import { planningUkrainian } from '../src/features/rotations/planningUkrainian.ts'

// A farmer may edit an assistant-proposed condition in the widget before confirming or rejecting it. The edit is
// the farmer's own choice in the app (`farmer_ui`): no quoted words, no model confidence, crops from this app's list.

const it = (name: string, run: () => Promise<void> | void) => test(name, { timeout: 5_000 }, run)
const edited = (type: string, value: unknown, extra: object = {}) => ({ id: 'k1', type, value, status: 'confirmed', source: 'farmer_ui', ...extra })

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

/** The planning catalogue the API serves (planning_catalog.py CROP_FAMILIES). */
function planningCrops(): string[] {
  const block = read('../../api/src/field_shift/modules/rotations/planning_catalog.py').match(/CROP_FAMILIES = \{([^}]*)\}/)?.[1] ?? ''
  return [...block.matchAll(/"([A-Za-z]+)":/g)].map((m) => m[1])
}

/** The agent's crop ids, the ones its simulated rotations use (agent_service/plain.py CROPS["en"]). */
function agentCrops(): string[] {
  const block = read('../../../services/agent/agent_service/plain.py').match(/^CROPS = \{[\s\S]*?"en": \{([^}]*)\}/m)?.[1] ?? ''
  return [...block.matchAll(/"([a-z_]+)":/g)].map((m) => m[1])
}

it('only crops that planning and the agent unmistakably share can be chosen in an edit', () => {
  const planning = planningCrops()
  const agent = agentCrops()
  assert.ok(planning.length > 0 && agent.length > 0, 'both catalogues were read')
  assert.deepEqual(Object.entries(SHARED_CROP_IDS).sort(), [['Maize', 'maize'], ['Soy', 'soybean'], ['Sunflower', 'sunflower']])
  for (const [crop, id] of Object.entries(SHARED_CROP_IDS)) {
    assert.ok(planning.includes(crop), `${crop} is a planning crop`)
    assert.ok(agent.includes(id), `${id} is an agent crop`)
  }
  // Every planning crop is either shared or left out with a reason: none is mapped by guesswork.
  assert.deepEqual([...Object.keys(SHARED_CROP_IDS), ...Object.keys(UNSHARED_PLANNING_CROPS)].sort(), [...planning].sort())
  // Generic wheat is not winter wheat, generic peas are not winter peas; barley and rapeseed have no agent crop.
  for (const id of ['wheat', 'winter_wheat', 'peas', 'winter_pea', 'barley', 'rapeseed', 'sorghum', 'winter_rye_vetch']) {
    assert.equal(EDITABLE_CROP_IDS.has(id), false, id)
  }
})

it('names the shared crops as the app does, in English and Ukrainian', () => {
  assert.deepEqual(editableCropOptions((crop) => crop), [
    { id: 'maize', label: 'Maize' }, { id: 'soybean', label: 'Soy' }, { id: 'sunflower', label: 'Sunflower' },
  ])
  const uk = editableCropOptions((crop) => planningUkrainian[crop] ?? crop)
  assert.deepEqual(uk.map((o) => o.label), ['Кукурудза', 'Соя', 'Соняшник'])
})

it("an edit carries no words the farmer did not say, and no model confidence", () => {
  assert.equal(editProblem(edited('irrigation_available', true), EDITABLE_CROP_IDS), null)
  assert.equal(editProblem(edited('irrigation_available', true, { evidence: 'Поливу немає' }), EDITABLE_CROP_IDS), 'invalid')
  assert.equal(editProblem(edited('rotation_length_years', 5, { confidence: 0.6 }), EDITABLE_CROP_IDS), 'invalid')
})

it('an edited value outside the contract or the crop list never reaches the API', () => {
  const bad = [
    edited('irrigation_available', 'yes'), edited('fertilizer_budget', 'lots'),
    edited('rotation_length_years', 9), edited('rotation_length_years', 1), edited('rotation_length_years', 3.5),
    edited('excluded_crops', []), edited('excluded_crops', ['winter_wheat']), edited('preferred_crops', ['sunflower', 'teff']),
    edited('excluded_crops', ['wheat']), edited('excluded_crops', ['peas']), edited('excluded_crops', ['rapeseed']),
    edited('preferred_crops', ['barley']), edited('previous_crop', 'winter_wheat'), edited('previous_crop', 'wheat'), edited('machinery_limit', '  '), edited('machinery_limit', ' padded '),
    edited('machinery_limit', 'x'.repeat(301)), edited('soil_ph', 6),
  ]
  for (const c of bad) assert.equal(editProblem(c, EDITABLE_CROP_IDS), 'invalid', JSON.stringify(c))
  const good = [
    edited('organic_only', false), edited('fertilizer_budget', 'low'), edited('rotation_length_years', 2),
    edited('excluded_crops', ['sunflower', 'maize']), edited('market_preference', ['soybean']), edited('previous_crop', 'maize'),
    edited('machinery_limit', 'Only a 6 m seeder'),
  ]
  for (const c of good) assert.equal(editProblem(c, EDITABLE_CROP_IDS), null, JSON.stringify(c))
})

it("the assistant's own proposals are sent as proposed, left to the API's checks", () => {
  const asProposed = { id: 'k1', type: 'excluded_crops', value: ['winter_wheat'], status: 'confirmed', source: 'farmer_text', evidence: 'no winter wheat' }
  assert.equal(editProblem(asProposed, EDITABLE_CROP_IDS), null)
})

function click(constraint: object, origin: 'farmer_click' | 'agent_auto' = 'farmer_click') {
  return {
    envelope: { id: 'confirm.k1', field_context_revision: 0, trigger: 'chip', label: 'Confirm', action: { type: 'compare.set_constraints', constraints: [constraint] } },
    fieldId: 'F-03',
    origin,
  }
}

it('the host refuses an invalid edited decision before sending it', () => {
  assert.equal(constraintDecision(click(edited('excluded_crops', ['winter_wheat'])), 'F-03', EDITABLE_CROP_IDS), 'invalid')
  assert.equal(constraintDecision(click(edited('irrigation_available', true, { evidence: 'made up' })), 'F-03', EDITABLE_CROP_IDS), 'invalid')
  const ok = constraintDecision(click(edited('excluded_crops', ['sunflower'])), 'F-03', EDITABLE_CROP_IDS)
  assert.notEqual(typeof ok, 'string')
  // Edited or not, only the farmer's click decides.
  assert.equal(constraintDecision(click(edited('excluded_crops', ['sunflower']), 'agent_auto'), 'F-03', EDITABLE_CROP_IDS), 'not_permitted')
  assert.equal(constraintDecision(click({ ...edited('excluded_crops', ['sunflower']), status: 'proposed' }), 'F-03', EDITABLE_CROP_IDS), 'not_permitted')
})

const KEY = '/agent/assistant/F-03/2026/baseline#0'
const apiError = (status: number, message: string, body: unknown = {}) => Object.assign(new Error(message), { status, body })
const tick = () => new Promise((resolve) => setImmediate(resolve))
const decisionFor = (constraint: object) => constraintDecision(click(constraint), 'F-03', EDITABLE_CROP_IDS) as ConstraintDecision

it("an edit that would replace an earlier decision still needs the farmer's approval of that exact decision", async () => {
  const earlier = { id: 'pc_excluded_crops', type: 'excluded_crops', value: ['sunflower'], status: 'confirmed', source: 'farmer_ui', token: '0b6f4a39-58c4-4d39-9f4d-3c8c25c2f6a1' }
  const body = decisionFor(edited('excluded_crops', ['sunflower', 'maize']))
  const asked = apiError(409, 'Confirm that this replaces your earlier decision.', {
    code: 'replacement_needs_confirmation', replacements: [{ current: earlier, proposed: edited('excluded_crops', ['sunflower', 'maize']) }],
  })
  const review = createReplacementReview<ConstraintReplacement>()
  review.sync(KEY, true)
  const sent: ConstraintDecision[] = []
  const outcome = decideWithReview(body, async (d) => { sent.push(d); if (sent.length === 1) throw asked }, (items) => review.ask(KEY, items))
  await tick()
  review.answer(true)
  assert.deepEqual(await outcome, { status: 'applied' })
  assert.deepEqual(sent[1].replaces, [{ type: 'excluded_crops', token: earlier.token }])
})

it('an edited decision that fails or went stale is reported, never shown as saved', async () => {
  const body = decisionFor(edited('rotation_length_years', 5))
  assert.deepEqual(await decideWithReview(body, async () => { throw apiError(503, 'Database is unavailable') }, async () => true),
    { status: 'rejected', reason: 'failed' })
  assert.deepEqual(await decideWithReview(body, async () => { throw apiError(409, 'The field boundary changed. Ask the assistant again.') }, async () => true),
    { status: 'rejected', reason: 'stale_context' })
  assert.deepEqual(await decideWithReview(body, async () => { throw apiError(422, 'Input should be a valid integer') }, async () => true),
    { status: 'rejected', reason: 'invalid' })
})
