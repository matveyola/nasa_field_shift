import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sceneHelpMetadata, spectralHelp } from '../src/features/fields/fieldHelp.ts'
import { spectralDefinitions } from '../src/features/fields/spectralIndices.ts'

test('every available spectral index has bilingual help and water indices distinguish soil moisture', () => {
  assert.deepEqual(Object.keys(spectralHelp).sort(), Object.keys(spectralDefinitions).sort())
  for (const help of Object.values(spectralHelp)) {
    assert.match(help.en, /unitless/)
    assert.match(help.uk, /не має одиниць/)
  }
  assert.match(spectralHelp.ndmi.en, /plants may be drier/)
  assert.match(spectralHelp.ndmi.uk, /рослини можуть бути сухішими/)
  assert.match(spectralHelp.ndwi.en, /open water/)
  assert.match(spectralHelp.ndwi.uk, /може стояти вода/)
  for (const { en, uk } of [spectralHelp.ndmi, spectralHelp.ndwi]) {
    assert.match(en, /does not measure soil moisture/)
    assert.match(uk, /не вимірює вологість ґрунту/)
  }
})

test('scene help displays supplied metadata and retains zero coverage as a known value', () => {
  const scene = { source: 'Sentinel-2 L2A', date: '2026-05-02', coveragePercent: 0, analysisResolutionMetres: 40 }
  const en = sceneHelpMetadata('en', scene).map(({ value }) => value)
  const uk = sceneHelpMetadata('uk', scene).map(({ value }) => value)
  assert.ok(en.includes(scene.source))
  assert.ok(uk.includes(scene.source))
  assert.ok(en.includes('0%'))
  assert.ok(uk.includes('0%'))
  assert.ok(en.includes('40 m'))
  assert.ok(uk.includes('40 м'))
  assert.ok(en.some((value) => value.includes('2026') && value.includes('May')))
  assert.ok(uk.some((value) => value.includes('2026') && value.includes('трав')))
})

test('scene help marks missing or invalid metadata as unknown', () => {
  const invalid = { source: ' ', date: 'invalid', coveragePercent: 101, analysisResolutionMetres: 0 }
  for (const metadata of [{}, invalid]) {
    assert.ok(sceneHelpMetadata('en', metadata).every(({ value }) => value === 'Unknown'))
    assert.ok(sceneHelpMetadata('uk', metadata).every(({ value }) => value === 'Невідомо'))
  }
})
