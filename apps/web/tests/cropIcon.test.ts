import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { cropIconKind } from '../src/shared/cropIconKind.ts'

const catalog = JSON.parse(readFileSync(new URL('../../../packages/field-shift-rotation-model/src/field_shift_rotation_model/modules/rotations/artifacts/forecast-crops.json', import.meta.url), 'utf8')) as { id: string; name: string }[]

test('every actual handoff catalog ID and name resolves to a botanical icon', () => {
  for (const item of catalog) {
    assert.ok(cropIconKind(item.id), item.id)
    assert.equal(cropIconKind(item.name), cropIconKind(item.id), item.name)
  }
})

test('allocation crop literals all resolve and retain their distinct species', () => {
  const source = readFileSync(new URL('../../../apps/api/src/field_shift/modules/rotations/planning_schemas.py', import.meta.url), 'utf8')
  const literal = source.match(/^Crop = Literal\[(.+)\]/m)
  assert.ok(literal, 'owning allocation Crop contract must be found')
  const crops = [...literal[1].matchAll(/"([^"]+)"/g)].map(match => match[1])
  assert.ok(crops.length)
  const kinds = crops.map(name => cropIconKind(name))
  assert.ok(kinds.every(Boolean))
  assert.equal(new Set(kinds).size, crops.length)
})

test('aliases normalize case and surrounding spaces but do not guess unknown species', () => {
  for (const [name, expected] of [[' CORN ', 'maize'], ['Soybean', 'soy'], ['Canola', 'rapeseed'], ['Dry peas', 'peas'], ['Potatoes', 'potato'], ['ЯРА ПШЕНИЦЯ', 'wheat']]) assert.equal(cropIconKind(name), expected)
  for (const name of [undefined, '', ' ', 'Rice', 'rye', 'constructor', '__proto__', 'QA-Maize', '<svg>', 'maize_grain_future']) assert.equal(cropIconKind(name), undefined)
})

test('all wheat management profiles share the existing wheat drawing without implying support', () => {
  for (const id of ['wheat_spring', 'wheat_winter_frozen', 'wheat_winter_nonfrozen']) assert.equal(cropIconKind(id), 'wheat')
})

