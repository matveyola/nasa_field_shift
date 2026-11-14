import assert from 'node:assert/strict'
import test from 'node:test'
import { rotationHelp } from '../src/features/rotations/rotationHelp.ts'
import { waterHelp, waterHelpMetadata } from '../src/features/water/waterHelp.ts'

test('water help distinguishes a near-surface ratio from water available to roots', () => {
  assert.match(waterHelp.en.surfaceMoisture.description, /m³\/m³/)
  assert.match(waterHelp.uk.surfaceMoisture.description, /м³\/м³/)
  assert.match(waterHelp.en.rootZoneReserve.description, /\bmm\b/)
  assert.match(waterHelp.uk.rootZoneReserve.description, /\sмм[.\s]/)
  for (const { rootZoneReserve } of Object.values(waterHelp)) {
    assert.doesNotMatch(rootZoneReserve.description, /[mм]³\/[mм]³/)
  }
  assert.match(waterHelp.en.stressDays.description, /below.*threshold/)
  assert.match(waterHelp.uk.stressDays.description, /нижчий за поріг/)
})

test('water help uses supplied source and depth and distinguishes unavailable metadata', () => {
  const known = waterHelpMetadata('en', { depth: '0–7 cm', source: 'ERA5-Land' })
  assert.match(known, /0–7 cm/)
  assert.match(known, /ERA5-Land/)
  assert.doesNotMatch(known, /unknown/)
  assert.match(waterHelpMetadata('en', { depth: ' ', source: 'IFS HRES' }), /Depth: unknown.*IFS HRES/)
  assert.match(waterHelpMetadata('uk', { depth: '0–7 см' }), /0–7 см.*Джерело даних: невідоме/)
  assert.match(waterHelpMetadata('uk', {}), /Глибина: невідома.*Джерело даних: невідоме/)
})

test('rotation help distinguishes weather bases and treats missing yield as unknown', () => {
  assert.match(rotationHelp.en.scenario.description, /historical comparison uses past weather.*forecast uses the weather expected/i)
  assert.match(rotationHelp.uk.scenario.description, /історичного порівняння.*минулу погоду.*Прогноз.*очікувану погоду/)
  assert.match(rotationHelp.en.yieldChange.description, /plus means more grain.*minus means less.*t\/ha.*prediction interval.*unknown/i)
  assert.match(rotationHelp.uk.yieldChange.description, /плюс означає більше зерна.*мінус.*менше.*т\/га.*інтервал прогнозу.*невідома/i)
})
