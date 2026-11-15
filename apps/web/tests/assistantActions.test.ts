import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { HOST_ACTIONS, HOST_SCREENS, hostStep } from '../src/features/assistant/assistantActions.ts'

test('the four declared actions take the farmer to this app\'s own views', () => {
  assert.deepEqual(hostStep({ type: 'map.set_layers', layer_ids: ['l_ndvi_variability'] }), { kind: 'vegetation', ndviLayer: true })
  assert.deepEqual(hostStep({ type: 'map.focus_field', field_id: 'F-01' }), { kind: 'screen', screen: 'fields' })
  assert.deepEqual(hostStep({ type: 'chart.focus', chart_id: 'c_vegetation', metric_ids: [] }), { kind: 'vegetation', ndviLayer: false })
  assert.deepEqual(hostStep({ type: 'chart.focus', chart_id: 'c_water_forecast', highlight: { start: '2026-06-14' } }),
    { kind: 'forecast', day: '2026-06-14' })
  // The season chart is a saved historical run: it opens that run, not whichever Water view was last shown.
  assert.deepEqual(hostStep({ type: 'chart.focus', chart_id: 'c_water_season' }), { kind: 'run' })
  assert.deepEqual(hostStep({ type: 'app.open_screen', screen_id: 'rotations' }), { kind: 'screen', screen: 'rotations' })
})

test('unknown layers, charts and screens are refused, never guessed', () => {
  assert.equal(hostStep({ type: 'map.set_layers', layer_ids: ['l_ndvi_variability', 'l_secret'] }), 'unknown_reference')
  assert.equal(hostStep({ type: 'map.set_layers', layer_ids: ['l_smap_rootzone'] }), 'unknown_reference')
  assert.equal(hostStep({ type: 'map.set_layers', layer_ids: 'l_ndvi_variability' }), 'unknown_reference')
  assert.equal(hostStep({ type: 'chart.focus', chart_id: 'c_other' }), 'unknown_reference')
  assert.equal(hostStep({ type: 'app.open_screen', screen_id: 'admin' }), 'unknown_reference')
  assert.deepEqual(hostStep({ type: 'chart.focus', chart_id: 'c_water_forecast', highlight: { start: 3 } }), { kind: 'forecast', day: undefined })
})

test('actions this host does not execute stay unavailable however they are offered', () => {
  for (const type of ['compare.set_constraints', 'compare.select_rotations', 'scenario.set', 'panel.open', 'time.set_range', 'made.up']) {
    assert.equal(hostStep({ type }), 'module_unavailable', type)
  }
})

test('the browser and the API declare the same host capabilities', () => {
  const signing = readFileSync(new URL('../../api/src/field_shift/modules/agent/signing.py', import.meta.url), 'utf8')
  const tuple = (name: string) => [...(signing.match(new RegExp(`^${name} = \\(([^)]*)\\)`, 'm'))?.[1] ?? '').matchAll(/"([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(tuple('HOST_ACTIONS'), [...HOST_ACTIONS])
  assert.deepEqual(tuple('HOST_SCREENS'), [...HOST_SCREENS])
})
