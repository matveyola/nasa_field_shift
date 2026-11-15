import assert from 'node:assert/strict'
import test from 'node:test'
import { initialVisualization, mapRequestResult, toggleVisualization, visibleVisualization } from '../src/features/fields/fieldMapVisualization.ts'

test('opening a vegetation deep link replaces the default thematic visualization', () => {
  assert.equal(initialVisualization(false), 'historical')
  assert.equal(initialVisualization(true), 'heterogeneity')
})

test('shortcuts replace the current visualization and reopen the empty view when toggled off', () => {
  let active = initialVisualization(false)
  active = toggleVisualization(active, 'heterogeneity')
  assert.equal(active, 'heterogeneity')
  active = toggleVisualization(active, 'terrain')
  assert.equal(active, 'terrain')
  active = toggleVisualization(active, 'satellite')
  assert.equal(active, 'satellite')
  assert.equal(toggleVisualization(active, 'satellite'), 'none')
})

test('switching to a field without planting or entering edit mode hides unavailable visualizations', () => {
  assert.equal(visibleVisualization('planting', true, true), 'planting')
  assert.equal(visibleVisualization('planting', true, false), 'none')
  assert.equal(visibleVisualization('historical', false, false), 'none')
  assert.equal(visibleVisualization('historical', true, false), 'historical')
})

test('a map resource accepts only the active request after changing field, layer or date', async () => {
  const first = { fieldId: 'A', layer: 'satellite', date: '2026-10-10' }
  let resolveFirst: (value: string) => void
  const pendingFirst = new Promise<string>((resolve) => { resolveFirst = resolve })
  const dated = { ...first, date: '2026-10-11' }
  const otherField = { ...dated, fieldId: 'B' }
  const terrain = { ...otherField, layer: 'terrain' }
  const latest = { request: terrain, value: 'terrain for B' }
  assert.equal(mapRequestResult(terrain, latest), 'terrain for B')

  resolveFirst!('older satellite image')
  const delayed = { request: first, value: await pendingFirst }
  assert.equal(mapRequestResult(dated, delayed), undefined)
  assert.equal(mapRequestResult(otherField, delayed), undefined)
  assert.equal(mapRequestResult(terrain, delayed), undefined)
  assert.equal(mapRequestResult(terrain, { request: otherField, value: 'old error' }), undefined)
  assert.equal(mapRequestResult(terrain, latest), 'terrain for B')
})
